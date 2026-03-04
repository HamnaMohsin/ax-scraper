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
    is_product_page = "aliexpress" in page_title_lower and len(page_title) > 40
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


# ── Shadow DOM extraction (JS) ─────────────────────────────────────────────────
#
# ROOT CAUSE OF MISSING TEXT — the selector+seen-Set approach:
#   When a broad selector like 'p' matches a <p> that has child <span>s,
#   el.innerText returns the COMBINED text of all children. This combined
#   string is added to `seen`. Later, when a specific selector tries the
#   individual child <span>s, their separate strings don't match the combined
#   parent string in `seen` → they get added again → duplicates.
#   Conversely if the parent was already processed, children are skipped → gaps.
#
# FIX — leaf-node traversal:
#   Walk every element in the shadow root. Only collect elements whose
#   .children.length === 0 (pure text leaves, no child elements).
#   This guarantees each text unit is collected exactly once, in document
#   order, with zero parent/child overlap.
#
# JUNK REMOVED BEFORE TRAVERSAL (so their text never reaches the collector):
#   style, script      — raw CSS/JS text would leak into textContent
#   .a-price           — price spans produce fragments like "$ 9 . 89"
#   .a-offscreen       — hidden duplicate price strings e.g. "$9.89"
#   .a-icon-alt        — screen-reader spans e.g. "4.5 out of 5 stars"
#   .comparison-table  — cross-sell table with repeated prices/specs
#   .premium-aplus-module-5 — same table, different class
#   .apm-brand-story-carousel-container — brand banner, not product desc
#   .vse-player-container — video section that only has "hero-video" text
#   .add-to-cart       — button text fragments

SHADOW_DOM_EXTRACT_JS = """
() => {
    const host = document.querySelector('#product-description [data-spm-anchor-id]');
    if (!host || !host.shadowRoot) return null;

    const root = host.shadowRoot;

    // ── Step 1: Strip all junk nodes before traversal ────────────────────────
    const junkSelectors = [
        'style', 'script',
        '.a-price', '.a-offscreen', '.a-icon-alt',
        '.comparison-table', '.premium-aplus-module-5',
        '.apm-brand-story-carousel-container',
        '.vse-player-container',
        '.add-to-cart',
        '.aplus-review-right-padding',
    ];
    junkSelectors.forEach(sel => {
        root.querySelectorAll(sel).forEach(el => el.remove());
    });

    // ── Step 2: Leaf-node text collection in document order ──────────────────
    // Only elements with zero element children are leaves — no parent/child overlap.
    const texts = [];
    const seenText = new Set();

    const allEls = root.querySelectorAll('p, h1, h2, h3, h4, h5, li, span, td, div');
    for (const el of allEls) {
        if (el.children.length > 0) continue;              // not a leaf

        const t = (el.innerText || el.textContent || '').trim();
        if (!t || t.length < 6) continue;                  // too short / empty

        if (/^[\d\s\.\,\$\€\£\¥\%\+\-]+$/.test(t)) continue; // pure number/symbol fragment

        if (seenText.has(t)) continue;                     // exact duplicate
        seenText.add(t);
        texts.push(t);
    }

    // ── Step 3: Images — alicdn only, deduplicated ───────────────────────────
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


# ── Fallback: plain DOM extraction ────────────────────────────────────────────

def extract_from_plain_dom(page) -> tuple:
    """
    Fallback for products that render description in the regular DOM
    (no shadow root). Uses the same leaf-node strategy as the JS above.
    """
    description_text = ""
    images = []

    container = page.query_selector("#product-description")
    if not container:
        print("Description container not found in plain DOM either.")
        return "", []

    print("Falling back to plain DOM extraction...")

    # Prefer known AliExpress description wrapper classes
    richtext = (
        container.query_selector(".detail-desc-decorate-richtext")
        or container.query_selector(".detailmodule_text")
        or container.query_selector(".detailmodule_html")
        or container
    )

    # Leaf-node text — same logic as JS
    for el in richtext.query_selector_all("p, span, li, h3, h4, div"):
        try:
            child_count = el.evaluate("e => e.children.length")
            text = el.text_content().strip()
            if child_count == 0 and text and len(text) >= 6:
                if not re.match(r'^[\d\s\.\,\$\€\£\¥\%\+\-]+$', text):
                    description_text += text + " "
        except Exception:
            pass

    # Images — dedicated containers first, then full container fallback
    for img in container.query_selector_all(
        "div.detailmodule_image img, "
        "div.detailmodule_html img, "
        "div.detail-desc-decorate-richtext img"
    ):
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        src = normalize_img_url(src)
        if "alicdn" in src and src not in images:
            images.append(src)

    if not images:
        for img in container.query_selector_all("img"):
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            src = normalize_img_url(src)
            if "alicdn" in src and src not in images:
                images.append(src)

    # Sanity: discard if text is mostly price data
    if description_text:
        dollar_ratio = description_text.count("$") / max(len(description_text), 1)
        if dollar_ratio > 0.02:
            print("Plain DOM text looks like price data — discarding.")
            description_text = ""

    return description_text.strip(), list(dict.fromkeys(images))


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
            print("Rotating Tor circuit before new attempt...")
            rotate_tor_circuit()
            random_delay(8.0, 15.0)

        with sync_playwright() as p:
            print("Opening browser...")
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

            if detect_recaptcha(page):
                print("CAPTCHA detected — rotating circuit and retrying.")
                browser.close()
                continue

            page.wait_for_timeout(8000)
            random_delay(2.0, 4.0)

            # ── Scroll gradually to trigger lazy loading ───────────────────────
            for _ in range(10):
                page.mouse.wheel(0, random.randint(150, 300))
                page.wait_for_timeout(random.randint(200, 500))

            random_delay(1.0, 3.0)

            if detect_recaptcha(page):
                print("CAPTCHA detected after scroll — rotating circuit and retrying.")
                browser.close()
                continue

            # ── Scroll description into view ───────────────────────────────────
            try:
                page.evaluate(
                    "document.querySelector('#product-description')?.scrollIntoView()"
                )
                page.wait_for_timeout(3000)
            except Exception:
                pass

            # ── Wait for shadow root XHR to populate content ─────────────────
            # AliExpress fires an XHR once the description section enters the
            # viewport. The shadow root exists immediately but stays empty until
            # the XHR completes. Poll until it has real text content.
            try:
                page.wait_for_function(
                    """() => {
                        const host = document.querySelector(
                            '#product-description [data-spm-anchor-id]'
                        );
                        if (!host || !host.shadowRoot) return false;
                        return (host.shadowRoot.textContent || '').trim().length > 50;
                    }""",
                    timeout=12000,
                )
                print("Shadow root populated — proceeding with extraction.")
            except Exception:
                print("Shadow root did not populate in 12s — will try fallbacks.")

            # ── Extract title ─────────────────────────────────────────────────
            def safe_query_text(selector: str) -> str:
                el = page.query_selector(selector)
                return el.text_content().strip() if el else ""

            BLOCKED_TITLES = {
                "aliexpress", "", "aanmelden", "sign in",
                "log in", "login", "verify", "robot",
            }
            title = ""
            title_selectors = [
                "[data-pl='product-title']",
                ".title--wrap--NWOaiSp h1",
                ".product-title-text",
            ]
            for sel in title_selectors:
                candidate = safe_query_text(sel)
                if candidate and candidate.lower().strip() not in BLOCKED_TITLES:
                    title = candidate
                    print(f"Title found via '{sel}': {title[:60]}")
                    break

            if not title:
                print("Title not found — page likely blocked.")
                browser.close()
                continue

            # ── Extract description + images ──────────────────────────────────
            description_text = ""
            images = []

            # Strategy 1: Shadow DOM (covers most modern AliExpress listings)
            try:
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                if result:
                    description_text = result.get("text", "").strip()
                    images = result.get("images", [])
                    if description_text or images:
                        print(
                            f"Shadow DOM: {len(description_text)} chars, "
                            f"{len(images)} images"
                        )
            except Exception as e:
                print(f"Shadow DOM extraction error: {e}")

            # Strategy 2: Plain DOM fallback (older product pages)
            if not description_text and not images:
                print("Shadow DOM returned nothing — trying plain DOM fallback...")
                description_text, images = extract_from_plain_dom(page)

            # Strategy 3: iframe fallback (rare edge case)
            if not description_text and not images:
                print("Trying iframe fallback...")
                try:
                    iframes = page.query_selector_all(
                        "#product-description iframe, "
                        "iframe[id*='desc'], iframe[name*='desc']"
                    )
                    for iframe_el in iframes:
                        frame = iframe_el.content_frame()
                        if not frame:
                            continue
                        frame.wait_for_load_state("domcontentloaded")
                        frame.wait_for_timeout(2000)

                        for el in frame.query_selector_all("p, span, div"):
                            try:
                                child_count = el.evaluate("e => e.children.length")
                                text = el.text_content().strip()
                                if child_count == 0 and text and len(text) >= 6:
                                    if not re.match(r'^[\d\s\.\,\$\€\£\¥\%\+\-]+$', text):
                                        description_text += text + " "
                            except Exception:
                                pass

                        for img in frame.query_selector_all("img"):
                            src = (
                                img.get_attribute("src")
                                or img.get_attribute("data-src")
                                or ""
                            )
                            src = normalize_img_url(src)
                            if "alicdn" in src:
                                images.append(src)

                        if description_text or images:
                            print(
                                f"iframe: {len(description_text)} chars, "
                                f"{len(images)} images"
                            )
                            break
                except Exception as e:
                    print(f"iframe fallback error: {e}")

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

    print(f"All {max_retries} attempts exhausted. Returning empty result.")
    return empty_result
