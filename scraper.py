import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────
# Human-like delay helper
# ─────────────────────────────────────────────
def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    """Sleep for a random duration to mimic human behaviour."""
    delay = random.uniform(min_sec, max_sec)
    print(f"Waiting {delay:.1f}s...")
    time.sleep(delay)


# ─────────────────────────────────────────────
# Tor circuit rotation via stem
# /etc/tor/torrc must have:
#   ControlPort 9051
#   CookieAuthentication 1
#   ExitNodes {us}
#   StrictNodes 1
# ─────────────────────────────────────────────
def rotate_tor_circuit():
    """Signal Tor to build a new circuit (new exit IP)."""
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("Tor circuit rotated — new exit IP assigned.")
            time.sleep(5)  # Tor needs time to establish new circuit
    except Exception as e:
        print(f"Failed to rotate Tor circuit: {e}")


# ─────────────────────────────────────────────
# CAPTCHA detection
# ─────────────────────────────────────────────
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

    page_title = page.title().lower()
    page_url = page.url.lower()
    if any(kw in page_title for kw in ["verify", "captcha", "robot", "blocked", "security"]):
        print(f"reCAPTCHA/block detected via page title: '{page.title()}'")
        return True
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print(f"reCAPTCHA/block detected via URL: '{page.url}'")
        return True

    return False


# ─────────────────────────────────────────────
# 2captcha solver (STUBBED — add API key to enable)
# To enable:
#   1. pip install 2captcha-python
#   2. Set TWO_CAPTCHA_API_KEY below
# ─────────────────────────────────────────────
TWO_CAPTCHA_API_KEY = ""  # <-- paste your key here when ready

def solve_recaptcha_2captcha(page) -> bool:
    if not TWO_CAPTCHA_API_KEY:
        print("2captcha skipped — no API key set.")
        return False

    try:
        from twocaptcha import TwoCaptcha
        solver = TwoCaptcha(TWO_CAPTCHA_API_KEY)

        sitekey = None
        recaptcha_div = page.query_selector(".g-recaptcha")
        if recaptcha_div:
            sitekey = recaptcha_div.get_attribute("data-sitekey")

        if not sitekey:
            iframe = page.query_selector("iframe[src*='recaptcha']")
            if iframe:
                src = iframe.get_attribute("src") or ""
                match = re.search(r"k=([A-Za-z0-9_-]+)", src)
                if match:
                    sitekey = match.group(1)

        if not sitekey:
            print("2captcha: could not find reCAPTCHA sitekey on page.")
            return False

        print(f"2captcha: solving reCAPTCHA with sitekey {sitekey[:20]}...")
        result = solver.recaptcha(sitekey=sitekey, url=page.url)
        token = result["code"]

        page.evaluate(f"""
            document.getElementById('g-recaptcha-response').innerHTML = '{token}';
            if (typeof ___grecaptcha_cfg !== 'undefined') {{
                Object.values(___grecaptcha_cfg.clients).forEach(client => {{
                    const cb = client?.l?.['']?.callback;
                    if (typeof cb === 'function') cb('{token}');
                }});
            }}
        """)
        page.wait_for_timeout(3000)
        print("2captcha: token injected successfully.")
        return True

    except Exception as e:
        print(f"2captcha solving failed: {e}")
        return False


# ─────────────────────────────────────────────
# URL / text helpers
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
# Main scraper
# ─────────────────────────────────────────────
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

        # Delay between retries to avoid rapid re-blocking
        if attempt > 1:
            random_delay(5.0, 12.0)

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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
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
                random_delay(3.0, 6.0)  # Human-like pause after page load
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                if attempt < max_retries:
                    rotate_tor_circuit()
                continue

            # ── CAPTCHA check immediately after load ──
            if detect_recaptcha(page):
                solved = solve_recaptcha_2captcha(page)
                if not solved:
                    browser.close()
                    if attempt < max_retries:
                        rotate_tor_circuit()
                    continue

            # ── Wait for JS/React to render ──
            page.wait_for_timeout(8000)
            random_delay(2.0, 4.0)

            # ── Scroll gradually to mimic human reading ──
            for _ in range(10):
                page.mouse.wheel(0, random.randint(150, 300))  # Vary scroll amount
                page.wait_for_timeout(random.randint(200, 600))  # Vary scroll speed

            random_delay(1.0, 3.0)

            # ── CAPTCHA check again after scroll ──
            if detect_recaptcha(page):
                solved = solve_recaptcha_2captcha(page)
                if not solved:
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
                    random_delay(1.0, 2.0)  # Pause before clicking
                    print("Clicking Description tab...")
                    desc_tab.click()
                    random_delay(3.0, 6.0)  # Wait for content to load after click
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
