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


def is_description_image(src: str) -> bool:
    s = src.lower()
    return "alicdn.com" in s or "aliexpress-media.com" in s


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Description extraction ─────────────────────────────────────────────────────
#
# WHY THE OLD CODE SKIPPED CONTENT:
#   The previous extraction used `child_count == 0` to only collect leaf nodes.
#   This skipped ANY element that had children — including <p> tags with <br>
#   children, <div> wrappers with nested <span>s, and all structured seller
#   descriptions. The result was empty description_text for most products.
#
# FIX — inner_text() + evaluate_all():
#   inner_text() is a browser-native method that walks the entire subtree,
#   handles <br> as line breaks, pierces shadow roots, and skips hidden
#   elements. It returns all visible text regardless of nesting depth or
#   class names — no selector assumptions needed.
#
#   evaluate_all() collects every <img> src in one JS call, also works
#   across shadow roots and nested structures.

def extract_description(page) -> tuple:
    """
    Extract description text and image URLs from #product-description.
    Works across all AliExpress layout variants:
      - Shadow DOM (Amazon A+ content)
      - detailmodule_html with nested p/span/br
      - richTextContainer plain DOM
      - Any future layout
    Returns (description_text, images).
    """
    description_text = ""
    images = []

    try:
        desc_container = page.locator("#product-description")

        if desc_container.count() == 0:
            print("  #product-description not found.")
            return "", []

        # ── Text ─────────────────────────────────────────────────────────────
        # inner_text() traverses the full subtree including shadow roots.
        # Handles <br> as newlines, skips hidden/script/style content.
        # No class names, no children.length filtering, no layout assumptions.
        try:
            raw_text = desc_container.inner_text(timeout=5000)
            description_text = re.sub(r"\s+", " ", raw_text).strip()
        except Exception as e:
            print(f"  inner_text() failed: {e}")

        # ── Images ───────────────────────────────────────────────────────────
        # evaluate_all() runs one JS call across all matched <img> elements.
        # Collects from both alicdn.com and aliexpress-media.com CDN domains.
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

            # ── Scroll to trigger lazy loads ───────────────────────────────────
            scroll_ok = safe_scroll(page, steps=12)
            if not scroll_ok:
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

            # ── Wait for title (replaces blind timeout) ───────────────────────
            TITLE_SELECTORS = [
                "[data-pl='product-title']",
                ".title--wrap--UUHae_g h1",
                ".title--wrap--NWOaiSp h1",
                ".product-title-text",
                "#root h1",
            ]
            title_appeared = False
            for sel in TITLE_SELECTORS:
                try:
                    page.wait_for_selector(sel, timeout=8000, state="visible")
                    title_appeared = True
                    break
                except Exception:
                    continue

            if not title_appeared:
                print("Title element never appeared — page blocked or too slow.")
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
            for sel in TITLE_SELECTORS:
                candidate = safe_text(sel)
                if candidate and candidate.lower().strip() not in BLOCKED:
                    title = candidate
                    print(f"Title via '{sel}': {title[:70]}")
                    break

            if not title:
                print("Title not found — page likely blocked.")
                browser.close()
                continue

            # ── Click #nav-description to trigger description XHR ─────────────
            # Without this click #product-description stays empty on most products.
            try:
                nav_desc = page.query_selector('#nav-description')
                if nav_desc:
                    nav_desc.scroll_into_view_if_needed()
                    random_delay(1.0, 2.0)
                    nav_desc.click(force=True)
                    print("Clicked #nav-description — waiting for content...")

                    # Wait for content to appear in the container (any layout).
                    try:
                        page.wait_for_function(
                            """() => {
                                const c = document.querySelector('#product-description');
                                if (!c) return false;
                                // Layout A/B: shadow root populated past CSS-only size
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
                            timeout=8000,
                        )
                        print("Description content loaded.")
                    except Exception:
                        print("XHR wait timed out — attempting extraction anyway...")

                    random_delay(1.0, 2.0)
                else:
                    print("#nav-description not found — description XHR won't fire.")
            except Exception as e:
                print(f"Could not click #nav-description: {e}")

            # ── Extract description + images ──────────────────────────────────
            description_text, images = extract_description(page)

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
