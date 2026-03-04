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
    is_product_page = (
        ("aliexpress" in page_title_lower or "aliexpress" in page.url.lower())
        and len(page_title) > 40
    )
    if not is_product_page:
        block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
        if any(kw in page_title_lower for kw in block_titles):
            print(f"Block detected via page title: '{page_title}'")
            return True

    return False


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
# CONFIRMED FROM DEBUG:
#   - Shadow host = anonymous <div> that is the DIRECT child of #product-description
#     i.e. `#product-description > div`  (it has NO id, NO data-spm-anchor-id)
#   - Shadow root contains CSS in a <style> tag + real content
#   - XHR only fires AFTER #nav-description is clicked — before click, shadow
#     root text is CSS-only (~3634 chars of stylesheet, no product text)
#   - After click, leaf nodes include real product description text + images
#
# JUNK TO STRIP (confirmed from debug leaves_sample):
#   style, script         raw CSS/JS leaks into textContent
#   .a-price              price fragments "$ 9 . 89"
#   .a-offscreen          hidden price dupes "$9.89"
#   .a-icon-alt           screen-reader text "4.5 out of 5 stars"
#   .comparison-table /
#   .premium-aplus-module-5   cross-sell comparison table
#   .apm-brand-story-carousel-container  brand banner
#   .vse-player-container     video section ("hero-video" text)
#   .add-to-cart              "Add to Cart" button text
#   .aplus-carousel-actions   "1 Slim & Lightweight 2 Full Protection" nav labels
#   .aplus-carousel-index     carousel index numbers "1", "2"

SHADOW_DOM_EXTRACT_JS = """
() => {
    // ── Find shadow host: direct anonymous child of #product-description ─────
    const container = document.querySelector('#product-description');
    if (!container) return { error: 'no #product-description' };

    // The host is the first (and only) child div — confirmed by debug output
    const host = container.querySelector(':scope > div');
    if (!host) return { error: 'no child div in #product-description' };
    if (!host.shadowRoot) return { error: 'child div has no shadowRoot', tag: host.tagName, id: host.id };

    const root = host.shadowRoot;

    // ── Strip junk nodes before traversal ────────────────────────────────────
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

    // ── Leaf-node text collection in document order ───────────────────────────
    // Only collect elements with zero element-children (pure text leaves).
    // This prevents parent+child text duplication entirely.
    const texts = [];
    const seenText = new Set();

    // Junk strings to skip even if they survive the removal pass
    const JUNK_STRINGS = new Set([
        'hero-video', 'product description', 'add to cart',
        'find more moko cases', 'customer reviews', 'price',
        'compatibility', 'material', 'features',
        'multi-color options', 'viewing & typing angles',
    ]);

    for (const el of root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div')) {
        if (el.children.length > 0) continue;

        const t = (el.innerText || el.textContent || '').trim();
        if (!t || t.length < 6) continue;

        // Skip pure number/symbol fragments (price decimals, ratings)
        if (/^[\d\s\.\,\$\€\£\¥\%\+\-\&nbsp;]+$/.test(t)) continue;

        // Skip known junk strings (case-insensitive)
        if (JUNK_STRINGS.has(t.toLowerCase())) continue;

        if (seenText.has(t)) continue;
        seenText.add(t);
        texts.push(t);
    }

    // ── Collect alicdn images ─────────────────────────────────────────────────
    const images = [];
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
                random_delay(3.0, 6.0)
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                continue

            print(f"Landed on: {page.url}")

            if detect_recaptcha(page):
                print("CAPTCHA detected — rotating circuit and retrying.")
                browser.close()
                continue

            # Wait for JS render
            page.wait_for_timeout(8000)
            random_delay(2.0, 4.0)

            # ── Scroll gradually to trigger lazy loads ─────────────────────────
            for _ in range(12):
                page.mouse.wheel(0, random.randint(200, 400))
                page.wait_for_timeout(random.randint(200, 400))

            random_delay(1.0, 2.0)

            if detect_recaptcha(page):
                print("CAPTCHA detected after scroll — retrying.")
                browser.close()
                continue

            # ── Extract title ─────────────────────────────────────────────────
            def safe_text(sel: str) -> str:
                el = page.query_selector(sel)
                return el.text_content().strip() if el else ""

            BLOCKED = {
                "aliexpress", "", "aanmelden", "sign in", "log in", "login", "verify", "robot"
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

            # ── CRITICAL: Click #nav-description to trigger description XHR ──
            # Confirmed by debug: before this click, #product-description > div
            # shadow root contains only CSS (~3634 chars). The actual product
            # description text is fetched via XHR only after this click fires.
            description_text = ""
            images = []

            try:
                nav_desc = page.query_selector('#nav-description')
                if nav_desc:
                    nav_desc.scroll_into_view_if_needed()
                    random_delay(1.0, 2.0)
                    nav_desc.click(force=True)
                    print("Clicked #nav-description — waiting for XHR...")

                    # Poll until shadow root has real content (not just CSS)
                    # CSS-only shadow root is ~3634 chars; real content pushes it much higher
                    try:
                        page.wait_for_function(
                            """() => {
                                const host = document.querySelector(
                                    '#product-description > div'
                                );
                                if (!host || !host.shadowRoot) return false;
                                const text = (host.shadowRoot.textContent || '').trim();
                                // Wait until content is beyond CSS-only length
                                return text.length > 4500;
                            }""",
                            timeout=15000,
                        )
                        print("Description XHR loaded — extracting...")
                    except Exception:
                        print("XHR wait timed out — attempting extraction anyway...")

                    random_delay(1.0, 2.0)
                else:
                    print("#nav-description not found — description may not load.")
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
                    print(f"Shadow DOM JS error: {result['error']}")
            except Exception as e:
                print(f"Shadow DOM evaluate error: {e}")

            # ── Fallback: plain DOM (older product pages without shadow root) ──
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

                        # Discard if mostly price data
                        if description_text:
                            dollar_ratio = description_text.count("$") / max(len(description_text), 1)
                            if dollar_ratio > 0.02:
                                print("Plain DOM text looks like price data — discarding.")
                                description_text = ""
                except Exception as e:
                    print(f"Plain DOM fallback error: {e}")

            images = list(dict.fromkeys(images))  # deduplicate, preserve order

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
