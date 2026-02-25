import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Sleep for a random duration to mimic human behaviour."""
    delay = random.uniform(min_sec, max_sec)
    print(f"Waiting {delay:.1f}s...")
    time.sleep(delay)


def rotate_tor_circuit():
    """Signal Tor to build a new circuit (new exit IP)."""
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
    """
    Returns True only if a real CAPTCHA/block page is detected.
    Checks DOM selectors and URL — avoids false positives on product titles.
    """
    # DOM-based checks (reliable — these only exist on block pages)
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

    # URL-based check (reliable — AliExpress block pages have these in the URL)
    page_url = page.url.lower()
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print(f"Block detected via URL: '{page.url}'")
        return True

    # Title check — only match if the page title looks like a block page,
    # NOT a product page. AliExpress product titles end with "- AliExpress"
    # followed by optional numbers. Block pages have short generic titles.
    page_title = page.title()
    page_title_lower = page_title.lower()
    is_product_page = "aliexpress" in page_title_lower and len(page_title) > 40
    if not is_product_page:
        block_titles = ["verify", "captcha", "robot", "access denied", "blocked"]
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


def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
    """
    Scrape an AliExpress product page for title, description text, and images.
    Routes through Tor SOCKS5 proxy with circuit rotation and human-like delays.
    """
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
                ]
            )
            context = browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
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

            # Mask the webdriver flag — primary trigger for bot detection
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # ── Navigate ──
            try:
                page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
                random_delay(3.0, 6.0)
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                if attempt < max_retries:
                    rotate_tor_circuit()
                continue

            # ── CAPTCHA check immediately after load ──
            if detect_recaptcha(page):
                print("CAPTCHA detected — rotating circuit and retrying.")
                browser.close()
                if attempt < max_retries:
                    rotate_tor_circuit()
                continue

            # ── Wait for JS/React to render ──
            page.wait_for_timeout(8000)
            random_delay(2.0, 4.0)

            # ── Scroll gradually to mimic human reading ──
            for _ in range(10):
                page.mouse.wheel(0, random.randint(150, 300))
                page.wait_for_timeout(random.randint(200, 600))

            random_delay(1.0, 3.0)

            # ── CAPTCHA check again after scroll ──
            if detect_recaptcha(page):
                print("CAPTCHA detected after scroll — rotating circuit and retrying.")
                browser.close()
                if attempt < max_retries:
                    rotate_tor_circuit()
                continue

            # ── Click Description tab ──
            try:
                desc_tab = (
                    page.query_selector('a:has-text("Description")') or
                    page.query_selector('a:has-text("description")')
                )
                if desc_tab:
                    random_delay(1.0, 2.0)
                    print("Clicking Description tab...")
                    # Try closing any open overlay first
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(1000)
                    except Exception:
                        pass
                    desc_tab.click(force=True)
                    random_delay(3.0, 6.0)
                    desc_container = page.query_selector("#product-description")
                    if desc_container:
                        desc_container.scroll_into_view_if_needed()
                        for _ in range(5):
                            page.mouse.wheel(0, random.randint(200, 400))
                            page.wait_for_timeout(random.randint(300, 700))
            except Exception as e:
                print(f"Could not click Description tab: {e}")

            random_delay(1.0, 2.0)

            # ── Extract title ──
            def safe_query_text(selector: str) -> str:
                el = page.query_selector(selector)
                return el.text_content().strip() if el else ""

            title = ""
            title_selectors = [
                "[data-pl='product-title']",
                ".product-title-text",
                ".title--wrap--UUHae_g h1",
                "h1.pdp-title",
                "#root h1",
                "h1",
            ]
            for sel in title_selectors:
                candidate = safe_query_text(sel)
                if candidate and candidate.lower().strip() != "aliexpress":
                    title = candidate
                    print(f"Title found with selector '{sel}': {title[:60]}")
                    break

            if not title:
                print("Title not found — page likely blocked. Rotating circuit...")
                browser.close()
                if attempt < max_retries:
                    rotate_tor_circuit()
                continue

            # ── Extract description + images ──
            description_text = ""
            images = []
            try:
                container = page.query_selector("#product-description")
                if container:
                    print("Found description container...")

                    text_elements = container.query_selector_all("p.detail-desc-decorate-content")
                    for el in text_elements:
                        text = el.text_content().strip()
                        if text:
                            description_text += text + " "

                    if not description_text:
                        for el in container.query_selector_all("p"):
                            text = el.text_content().strip()
                            if text:
                                description_text += text + " "

                    for img in container.query_selector_all("img"):
                        src = img.get_attribute("src") or img.get_attribute("data-src")
                        if src:
                            src = normalize_img_url(src)
                            if "alicdn" in src:
                                images.append(src)

                    images = list(dict.fromkeys(images))
                    print(f"Extracted {len(images)} images, description length: {len(description_text)}")
                else:
                    print("Description container not found.")
            except Exception as e:
                print(f"Error extracting description/images: {e}")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": images
            }

    print(f"All {max_retries} attempts exhausted. Returning empty result.")
    return empty_result
