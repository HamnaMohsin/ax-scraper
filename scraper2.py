import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# ── Helpers ────────────────────────────────────────────────────────────────────

def random_delay(min_sec: float = 0.5, max_sec: float = 1.5):
    time.sleep(random.uniform(min_sec, max_sec))


def rotate_tor_circuit():
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("Tor circuit rotated.")
            time.sleep(5)
    except Exception as e:
        print(f"Failed to rotate Tor circuit: {e}")


def is_aliexpress_url(url: str) -> bool:
    return "aliexpress." in url.lower()


def is_description_image(src: str) -> bool:
    s = src.lower()
    return "alicdn.com" in s or "aliexpress-media.com" in s


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
                print(f"Block detected: {selector}")
                return True
        except Exception:
            pass

    page_url = page.url.lower()
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print(f"Block detected via URL: '{page.url}'")
        return True

    page_title = page.title()
    is_product_page = is_aliexpress_url(page.url) and len(page_title) > 40
    if not is_product_page:
        block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
        if any(kw in page_title.lower() for kw in block_titles):
            print(f"Block detected via title: '{page_title}'")
            return True

    return False


def safe_scroll(page, steps: int = 8) -> bool:
    for _ in range(steps):
        try:
            if page.is_closed():
                return False
            page.mouse.wheel(0, random.randint(250, 450))
            page.wait_for_timeout(150)
        except Exception as e:
            print(f"Scroll interrupted: {e}")
            return False
    return True


def random_viewport():
    return {
        "width": random.choice([1280, 1366, 1440, 1536]),
        "height": random.choice([720, 768, 900]),
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


# ── Stealth init script ────────────────────────────────────────────────────────

STEALTH_INIT_SCRIPT = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
        ],
    });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth',  { get: () => 24 });
    Object.defineProperty(navigator, 'userAgent', {
        get: () => navigator.userAgent.replace('HeadlessChrome', 'Chrome'),
    });
}
"""


# ── Description extraction ─────────────────────────────────────────────────────
#
# WHY inner_text() INSTEAD OF JS TRAVERSAL:
#
#   AliExpress has 3+ different description HTML layouts depending on seller:
#     A) Amazon A+ content  → shadow root with carousel modules
#     B) Plain richtext     → shadow root with detailmodule_html > p+br
#     C) richTextContainer  → plain DOM second child div
#     D) detailmodule_html  → plain DOM, no shadow root at all (this bug)
#
#   Trying to enumerate every layout with JS selectors is fragile —
#   every new product can break the scraper. Playwright's inner_text()
#   pierces shadow DOM automatically and returns all visible text from
#   the entire subtree regardless of nesting depth or class names.
#
#   For images: evaluate_all() on the img locator collects every <img>
#   inside the container, including those nested inside shadow roots,
#   plain divs, or any other structure — no selector guessing needed.

def extract_description(page) -> tuple[str, list[str]]:
    """
    Extract description text and image URLs from #product-description.
    Works across all known AliExpress layout variants.
    Returns (description_text, images).
    """
    description_text = ""
    images = []

    try:
        desc_container = page.locator("#product-description")

        # Confirm the container exists and is visible
        if desc_container.count() == 0:
            print("  #product-description not found.")
            return "", []

        # ── Text ─────────────────────────────────────────────────────────────
        # inner_text() traverses the full subtree including shadow roots,
        # respects <br> as line breaks, and skips hidden elements.
        # No class names, no children.length checks, no layout assumptions.
        try:
            raw_text = desc_container.inner_text(timeout=5000)
            description_text = re.sub(r"\s+", " ", raw_text).strip()
        except Exception as e:
            print(f"  inner_text() failed: {e}")

        # ── Images ───────────────────────────────────────────────────────────
        # evaluate_all() runs one JS call over all matched elements —
        # faster than iterating in Python and handles shadow DOM naturally.
        try:
            raw_srcs = desc_container.locator("img").evaluate_all(
                "imgs => imgs.map(img => img.src || img.getAttribute('data-src') || '')"
            )
            for src in raw_srcs:
                src = normalize_img_url(src)
                if src and is_description_image(src) and src not in images:
                    images.append(src)
        except Exception as e:
            print(f"  Image collection failed: {e}")

        print(f"  Extracted: {len(description_text)} chars, {len(images)} images")

    except Exception as e:
        print(f"  extract_description error: {e}")

    return description_text, images


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
            random_delay(5.0, 8.0)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            context = browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
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
            page.add_init_script(f"({STEALTH_INIT_SCRIPT})()")

            # ── Navigate ──────────────────────────────────────────────────────
            try:
                page.goto(base_url, timeout=90000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                continue

            print(f"Landed on: {page.url}")

            if not is_aliexpress_url(page.url):
                print("Redirected off AliExpress — skipping.")
                browser.close()
                continue

            page.wait_for_timeout(5000)

            if detect_recaptcha(page):
                print("CAPTCHA detected — retrying.")
                browser.close()
                continue

            if not safe_scroll(page, steps=8):
                print("Scroll failed — retrying.")
                try:
                    browser.close()
                except Exception:
                    pass
                continue

            if page.is_closed():
                browser.close()
                continue

            if detect_recaptcha(page):
                print("CAPTCHA after scroll — retrying.")
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
                    print(f"Title: {title[:70]}")
                    break

            if not title:
                print("Title not found — page likely blocked.")
                browser.close()
                continue

            # ── Click #nav-description to fire the description XHR ───────────
            # Without this click, #product-description is empty on most products.
            try:
                nav_desc = page.query_selector('#nav-description')
                if nav_desc:
                    nav_desc.scroll_into_view_if_needed()
                    random_delay(0.5, 1.0)
                    nav_desc.click(force=True)
                    print("Clicked #nav-description — waiting for content...")

                    # Wait until the container has real content.
                    # Covers all layouts: shadow root (A/B) and plain DOM (C/D).
                    try:
                        page.wait_for_function(
                            """() => {
                                const c = document.querySelector('#product-description');
                                if (!c) return false;

                                // Layout A/B: shadow root populated
                                const host = c.querySelector(':scope > div');
                                if (host && host.shadowRoot) {
                                    if ((host.shadowRoot.textContent || '').trim().length > 4500)
                                        return true;
                                }

                                // Layout C/D: plain DOM has content
                                if ((c.textContent || '').trim().length > 50)
                                    return true;

                                return false;
                            }""",
                            timeout=12000,
                        )
                        print("Description content detected.")
                    except Exception:
                        print("Wait timed out — extracting anyway...")

                    random_delay(0.5, 1.0)
                else:
                    print("#nav-description not found.")
            except Exception as e:
                print(f"Could not click #nav-description: {e}")

            # ── Extract description + images ──────────────────────────────────
            description_text, images = extract_description(page)

            images = list(dict.fromkeys(images))  # deduplicate, preserve order

            if not description_text:
                print("No description text.")
            if not images:
                print("No description images.")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": images,
            }

    print(f"All {max_retries} attempts exhausted.")
    return empty_result
