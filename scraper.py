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


# ── Strategy 1: Shadow DOM (JS) ───────────────────────────────────────────────

SHADOW_DOM_EXTRACT_JS = """
() => {
    const host = document.querySelector('#product-description [data-spm-anchor-id]');
    if (!host || !host.shadowRoot) return null;

    const root = host.shadowRoot;

    // Remove comparison/price table noise
    const junkSelectors = [
        '.comparison-table',
        '.premium-aplus-module-5',
        '.apm-brand-story-carousel-container',
    ];
    junkSelectors.forEach(sel => {
        root.querySelectorAll(sel).forEach(el => el.remove());
    });

    const textSelectors = [
        '.aplus-p1',
        '.aplus-p3',
        '.aplus-description',
        'h3',
        'h4.aplus-h1',
        'h1.aplus-h3',
        '.card-description p',
        '.column-description p',
        'p',
    ];

    const seen = new Set();
    let text = '';
    for (const sel of textSelectors) {
        root.querySelectorAll(sel).forEach(el => {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 5 && !seen.has(t)) {
                seen.add(t);
                text += t + ' ';
            }
        });
    }

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

    return { text: text.trim(), images };
}
"""


# ── Strategy 2: Plain DOM extraction ─────────────────────────────────────────

def extract_from_plain_dom(page) -> tuple:
    description_text = ""
    images = []

    container = page.query_selector("#product-description")
    if not container:
        print("Description container not found in plain DOM either.")
        return "", []

    print("Falling back to plain DOM extraction...")

    # ── Images ────────────────────────────────────────────────────────────────
    # Search all known image-bearing module containers.
    # These are sibling divs — images are NOT always inside the text container.
    IMAGE_SELECTORS = (
        "div.detailmodule_image img, "
        "div.detailmodule_html img, "
        "div.detail-desc-decorate-richtext img, "
        "div.richTextContainer img, "
        "div.styleIsolation img, "
        "[data-rich-text-render] img"
    )
    for img in container.query_selector_all(IMAGE_SELECTORS):
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        src = normalize_img_url(src)
        if "alicdn" in src and src not in images:
            images.append(src)

    # Fallback: grab all alicdn images anywhere in the container
    if not images:
        for img in container.query_selector_all("img"):
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            src = normalize_img_url(src)
            if "alicdn" in src and src not in images:
                images.append(src)

    if images:
        print(f"Plain DOM: found {len(images)} images")

    # ── Text ──────────────────────────────────────────────────────────────────
    # Try most specific selector first, then progressively broader fallbacks.

    # Priority 1: exact AliExpress paragraph class
    text_container = (
        container.query_selector(".detailmodule_text") or
        container.query_selector(".detail-desc-decorate-richtext") or
        container.query_selector(".detailmodule_html") or
        container.query_selector(".richTextContainer") or
        container.query_selector(".styleIsolation") or
        container
    )

    specific_els = text_container.query_selector_all("p.detail-desc-decorate-content")
    if specific_els:
        for el in specific_els:
            text = el.text_content().strip()
            if text and len(text) > 5:
                description_text += text + " "
        print("Plain DOM: text via p.detail-desc-decorate-content")

    # Priority 2: richTextContainer / styleIsolation — text is raw HTML with <br>
    # tags, not wrapped in <p> elements, so read innerText directly
    if not description_text:
        for sel in ("div.richTextContainer", "div.styleIsolation"):
            for el in container.query_selector_all(sel):
                try:
                    text = el.evaluate("e => e.innerText").strip()
                    if text and len(text) > 5:
                        description_text += text + " "
                except Exception:
                    pass
        if description_text:
            print("Plain DOM: text via richTextContainer/styleIsolation innerText")

    # Priority 3: leaf-node fallback across all known text containers
    if not description_text:
        for el in text_container.query_selector_all("p, li, h3, h4"):
            try:
                child_count = el.evaluate("e => e.children.length")
                text = el.text_content().strip()
                if child_count == 0 and text and len(text) > 5:
                    description_text += text + " "
            except Exception:
                pass

    # Discard if mostly price comparison data
    if description_text:
        dollar_ratio = description_text.count("$") / max(len(description_text), 1)
        if dollar_ratio > 0.02:
            print("Plain DOM: text looks like price data — discarding.")
            description_text = ""

    if description_text:
        print(f"Plain DOM: found {len(description_text)} chars of text")

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

            # ── Wait for description content to load ──────────────────────────
            # Checks BOTH shadow DOM and plain DOM so it works for all product types.
            # Without this, extraction runs before AliExpress has injected the content.
            try:
                page.wait_for_function(
                    """() => {
                        const container = document.querySelector('#product-description');
                        if (!container) return false;

                        // Shadow DOM products (A+ content)
                        const host = container.querySelector('[data-spm-anchor-id]');
                        if (host && host.shadowRoot) {
                            return (host.shadowRoot.textContent || '').trim().length > 50;
                        }

                        // Plain DOM products — check all known content containers
                        const plainSelectors = [
                            '.detailmodule_text',
                            '.detailmodule_image',
                            '.detailmodule_html',
                            '.detail-desc-decorate-richtext',
                            '.richTextContainer',
                            '.styleIsolation',
                        ];
                        for (const sel of plainSelectors) {
                            const el = container.querySelector(sel);
                            if (el && el.textContent.trim().length > 20) return true;
                        }
                        return false;
                    }""",
                    timeout=12000,
                )
                print("Description content loaded — proceeding with extraction.")
            except Exception:
                print("Description did not load in 12s — attempting extraction anyway.")

            # ── Extract title ─────────────────────────────────────────────────
            def safe_query_text(selector: str) -> str:
                el = page.query_selector(selector)
                return el.text_content().strip() if el else ""

            title = ""
            BLOCKED_TITLES = {"aliexpress", "", "aanmelden", "sign in", "log in", "login", "verify", "robot"}
            title_selectors = [
                "[data-pl='product-title']",
                ".title--wrap--NWOaiSp h1",
                ".product-title-text",
                ".title--wrap--UUHae_g h1",
                "h1.pdp-title",
                "#root h1",
                "h1",
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

            # Strategy 1: Shadow DOM (A+ content products)
            try:
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                if result:
                    description_text = result.get("text", "").strip()
                    images = result.get("images", [])
                    if description_text or images:
                        print(f"Shadow DOM: {len(description_text)} chars, {len(images)} images")
            except Exception as e:
                print(f"Shadow DOM extraction error: {e}")

            # Strategy 2: Plain DOM fallback (standard products)
            if not description_text and not images:
                print("Shadow DOM returned nothing — trying plain DOM fallback...")
                description_text, images = extract_from_plain_dom(page)

            # Strategy 3: iframe fallback (rare sellers)
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
                                if child_count == 0 and text and len(text) > 5:
                                    description_text += text + " "
                            except Exception:
                                pass

                        for img in frame.query_selector_all("img"):
                            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                            src = normalize_img_url(src)
                            if "alicdn" in src:
                                images.append(src)

                        if description_text or images:
                            print(f"iframe: {len(description_text)} chars, {len(images)} images")
                            break
                except Exception as e:
                    print(f"iframe fallback error: {e}")

            images = list(dict.fromkeys(images))

            if not description_text:
                print("No description text extracted (seller may use image-only description).")
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
