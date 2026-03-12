import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# ── Helpers ────────────────────────────────────────────────────────────────────

def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    delay = random.uniform(min_sec, max_sec)
    print(f"Waiting {delay:.1f}s...")
    time.sleep(delay)


def rotate_tor_circuit():
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("Tor circuit rotated — new exit IP assigned.")
            time.sleep(5)
    except Exception as e:
        print(f"Failed to rotate Tor circuit: {e}")


def is_aliexpress_url(url: str) -> bool:
    """Accept any regional AliExpress domain: .com, .us, .co.uk, .it, etc."""
    return "aliexpress." in url.lower()


def detect_recaptcha(page) -> bool:
    indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='google.com/recaptcha']",
        ".g-recaptcha",
        "#captcha-verify",
        ".baxia-punish",
        "[id*='captcha']",
    ]
    for selector in indicators:
        try:
            if page.query_selector(selector):
                print(f"reCAPTCHA/block detected via selector: {selector}")
                return True
        except Exception:
            pass

    page_url = page.url.lower()
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print(f"Block detected via URL: '{page.url}'")
        return True

    page_title = page.title()
    page_title_lower = page_title.lower()
    is_product_page = is_aliexpress_url(page.url) and len(page_title) > 40
    if not is_product_page:
        block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
        if any(kw in page_title_lower for kw in block_titles):
            print(f"Block detected via page title: '{page_title}'")
            return True

    return False


def safe_scroll(page, steps: int = 12) -> bool:
    """
    Scroll gradually. Returns False if the page closed mid-scroll
    (happens when AliExpress fires a mid-page redirect).
    """
    for _ in range(steps):
        try:
            if page.is_closed():
                print("Page closed during scroll — likely a redirect.")
                return False
            page.mouse.wheel(0, random.randint(200, 400))
            page.wait_for_timeout(random.randint(200, 400))
        except Exception as e:
            print(f"Scroll interrupted: {e}")
            return False
    return True


def random_viewport():
    return {
        "width": random.choice([1280, 1366, 1440, 1536, 1600]),
        "height": random.choice([720, 768, 864, 900]),
    }


def normalize_img_url(src: str) -> str:
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.aliexpress.com" + src
    return src


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Shadow DOM extraction ──────────────────────────────────────────────────────
#
# CONFIRMED FROM DEBUG OUTPUT:
#   - Shadow host = `#product-description > div`  (anonymous div, no id/attrs)
#   - Before #nav-description click: shadow root = CSS only (~3634 chars)
#   - After click: XHR fires, real content appears, text.length > 4500
#   - Leaf nodes confirmed: product description paragraphs + feature headings
#   - Junk confirmed: hero-video, Add to Cart (x4), Find More MoKo Cases,
#     comparison table prices, brand carousel duplicates

SHADOW_DOM_EXTRACT_JS = """
() => {
    const container = document.querySelector('#product-description');
    if (!container) return { error: 'no #product-description' };

    const host = container.querySelector(':scope > div');
    if (!host)       return { error: 'no child div in #product-description' };
    if (!host.shadowRoot) return { error: 'no shadowRoot on child div' };

    const root = host.shadowRoot;

    // Strip junk before traversal so their text never reaches the collector
    [
        'style', 'script',
        '.a-price', '.a-offscreen', '.a-icon-alt',
        '.comparison-table', '.premium-aplus-module-5',
        '.apm-brand-story-carousel-container',
        '.vse-player-container',
        '.add-to-cart',
        '.aplus-carousel-actions',
        '.aplus-carousel-index',
        '.aplus-review-right-padding',
    ].forEach(sel => root.querySelectorAll(sel).forEach(el => el.remove()));

    // Leaf-node text collection — only elements with zero child elements
    const JUNK = new Set([
        'hero-video', 'product description', 'add to cart',
        'find more moko cases', 'customer reviews', 'price',
        'compatibility', 'material', 'features',
        'multi-color options', 'viewing & typing angles',
    ]);

    const texts = [];
    const seen  = new Set();

    for (const el of root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div')) {
        function isCollectable(el) {
        if (el.children.length === 0) return true;
        return Array.from(el.children).every(c => c.tagName === 'BR');}
        if (!isCollectable(el)) continue;

        const t = (el.innerText || el.textContent || '').trim();
        if (!t || t.length < 6) continue;
        if (/^[\d\s\.\,\$\€\£\¥\%\+\-\&nbsp;]+$/.test(t)) continue;
        if (JUNK.has(t.toLowerCase())) continue;
        if (seen.has(t)) continue;

        seen.add(t);
        texts.push(t);
    }

    // alicdn images — deduplicated
    const images  = [];
    const seenSrc = new Set();
    root.querySelectorAll('img').forEach(img => {
        let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
        if (!src) return;
        src = src.trim();
        if (src.startsWith('//')) src = 'https:' + src;
        if (src.includes('alicdn') && !seenSrc.has(src)) {
            seenSrc.add(src);
            images.push(src);
        }
    });

    return { text: texts.join(' '), images };
}
"""


# ── Main scraper ───────────────────────────────────────────────────────────────

def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
    print("Starting scrape...")

    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    empty_result = {"title": "", "description_text": "", "images": []}

    for attempt in range(1, max_retries + 1):
        print(f"\n── Attempt {attempt}/{max_retries} ──")

        if attempt > 1:
            rotate_tor_circuit()
            random_delay(8.0, 15.0)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                ]
            )
            context = browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                ]),
                viewport=random_viewport(),
                locale="en-US",
                timezone_id=random.choice([
                    "America/New_York",
                    "America/Chicago",
                    "America/Los_Angeles",
                ]),
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                java_script_enabled=True,
                bypass_csp=True,
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            # ── Navigate ──────────────────────────────────────────────────────
            try:
                page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                continue

            print(f"Landed on: {page.url}")

            # Reject if we ended up off AliExpress entirely
            if not is_aliexpress_url(page.url):
                print(f"Redirected off AliExpress to {page.url} — skipping.")
                browser.close()
                continue

            random_delay(3.0, 6.0)

            if detect_recaptcha(page):
                print("CAPTCHA detected — retrying.")
                browser.close()
                continue

            # Wait for initial JS render
            page.wait_for_timeout(8000)
            random_delay(1.0, 3.0)

            # ── Scroll — wrapped so a mid-redirect crash is handled cleanly ──
            scroll_ok = safe_scroll(page, steps=12)
            if not scroll_ok:
                # Page was closed by a redirect; re-open on the new URL
                # (browser is still alive, just that page closed)
                print("Scroll failed — page likely redirected. Retrying attempt...")
                try:
                    browser.close()
                except Exception:
                    pass
                continue

            random_delay(1.0, 2.0)

            if page.is_closed():
                print("Page closed unexpectedly after scroll.")
                browser.close()
                continue

            if detect_recaptcha(page):
                print("CAPTCHA detected after scroll — retrying.")
                browser.close()
                continue

            # ── Extract title ─────────────────────────────────────────────────
            def safe_text(sel: str) -> str:
                try:
                    el = page.query_selector(sel)
                    return el.text_content().strip() if el else ""
                except Exception:
                    return ""

            BLOCKED = {
                "aliexpress", "", "aanmelden", "sign in",
                "log in", "login", "verify", "robot",
            }
            title = ""
            for sel in [
                "[data-pl='product-title']",
                ".title--wrap--UUHae_g h1",
                ".title--wrap--NWOaiSp h1",
                ".product-title-text",
                "#root h1",
            ]:
                candidate = safe_text(sel)
                if candidate and candidate.lower().strip() not in BLOCKED:
                    title = candidate
                    print(f"Title via '{sel}': {title[:70]}")
                    break

            if not title:
                print("Title not found — page likely blocked.")
                browser.close()
                continue

            # ── Click #nav-description to trigger description XHR ────────────
            description_text = ""
            images = []

            try:
                nav_desc = page.query_selector('#nav-description')
                if nav_desc:
                    nav_desc.scroll_into_view_if_needed()
                    random_delay(1.0, 2.0)
                    nav_desc.click(force=True)
                    print("Clicked #nav-description — waiting for XHR...")

                    # Poll until shadow root text exceeds CSS-only length (~3634)
                    try:
                        page.wait_for_function(
                            """() => {
                                const host = document.querySelector(
                                    '#product-description > div'
                                );
                                if (!host || !host.shadowRoot) return false;
                                return (host.shadowRoot.textContent || '').trim().length > 4500;
                            }""",
                            timeout=15000,
                        )
                        print("Description content loaded.")
                    except Exception:
                        print("XHR wait timed out — attempting extraction anyway...")

                    random_delay(1.0, 2.0)
                else:
                    print("#nav-description not found — description XHR won't fire.")
            except Exception as e:
                print(f"Could not click #nav-description: {e}")

            # ── Extract via Shadow DOM JS ─────────────────────────────────────
            try:
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                if result and "error" not in result:
                    description_text = result.get("text", "").strip()
                    images = result.get("images", [])
                    print(f"Shadow DOM: {len(description_text)} chars, {len(images)} images")
                elif result and "error" in result:
                    print(f"Shadow DOM JS returned: {result['error']}")
            except Exception as e:
                print(f"Shadow DOM evaluate error: {e}")

            # ── Fallback: plain DOM (older pages without shadow root) ─────────
            if not description_text and not images:
                print("Shadow DOM empty — trying plain DOM fallback...")
                try:
                    container = page.query_selector("#product-description")
                    if container:
                        for el in container.query_selector_all("p, span, li, h3, h4, div"):
                            try:
                                child_count = el.evaluate("e => e.children.length")
                                text = el.text_content().strip()
                                if child_count == 0 and text and len(text) >= 6:
                                    if not re.match(r'^[\d\s\.\,\$\€\£\¥\%\+\-]+$', text):
                                        description_text += text + " "
                            except Exception:
                                pass

                        for img in container.query_selector_all("img"):
                            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                            src = normalize_img_url(src)
                            if "alicdn" in src:
                                images.append(src)

                        if description_text:
                            dollar_ratio = description_text.count("$") / max(len(description_text), 1)
                            if dollar_ratio > 0.02:
                                print("Plain DOM text looks like price data — discarding.")
                                description_text = ""
                except Exception as e:
                    print(f"Plain DOM fallback error: {e}")

            images = list(dict.fromkeys(images))

            if not description_text:
                print("No description text (seller may use image-only description).")
            if not images:
                print("No description images extracted.")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": images,
            }

    print(f"All {max_retries} attempts exhausted.")
    return empty_result
