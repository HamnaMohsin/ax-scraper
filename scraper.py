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



# ── Fallback: plain DOM extraction ────────────────────────────────────────────

def extract_from_plain_dom(page) -> tuple:
    description_text = ""
    images = []

    container = page.query_selector("#product-description")
    if not container:
        print("Description container not found in plain DOM either.")
        return "", []

    print("Falling back to plain DOM extraction...")

    # Walk every direct child module of #product-description in order.
    # AliExpress interleaves text and image modules arbitrarily — e.g:
    #   div.detailmodule_image → imgs
    #   div.detailmodule_text  → paragraphs
    #   div.richTextContainer  → mixed rich text
    #   div.detailmodule_image → more imgs
    # We extract from each module as we encounter it to preserve order.

    modules = container.query_selector_all(
        "div.detailmodule_image, "
        "div.detailmodule_text, "
        "div.detailmodule_html, "
        "div.detail-desc-decorate-richtext, "
        "div.richTextContainer"
    )

    if not modules:
        # No known module classes — fall back to scanning the whole container
        modules = [container]

    for module in modules:
        # ── Images ──
        for img in module.query_selector_all("img"):
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            src = normalize_img_url(src)
            if "alicdn" in src and src not in images:
                images.append(src)

        # ── Text ──
        # Try the specific AliExpress paragraph class first
        text_els = module.query_selector_all("p.detail-desc-decorate-content")
        if not text_els:
            # Fall back to any leaf-level text nodes
            text_els = module.query_selector_all("p, span, li, h3, h4")

        for el in text_els:
            try:
                child_count = el.evaluate("e => e.children.length")
                text = el.text_content().strip()
                if child_count == 0 and text and len(text) > 5:
                    description_text += text + " "
            except Exception:
                pass

    # Sanity check: discard text if it's mostly price comparison data
    if description_text:
        dollar_ratio = description_text.count("$") / max(len(description_text), 1)
        if dollar_ratio > 0.02:
            print("Plain DOM text looks like price data — discarding.")
            description_text = ""

    if images:
        print(f"Plain DOM: found {len(images)} images")
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

            # Strategy 1: Plain DOM
            description_text, images = extract_from_plain_dom(page)

            # Strategy 2: iframe fallback
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
