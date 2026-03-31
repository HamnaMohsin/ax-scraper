"""
AliExpress product scraper — improved merged version.
Uses Camoufox + Playwright + Tor for anti-detection.
"""

import re
import time
import random
import traceback

from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_IMAGE_DOMAINS = ["alicdn.com", "ae01.alicdn.com", "m.media-amazon.com", "amazonaws.com"]
BAD_IMAGE_PATTERNS  = ["icon", "logo", "avatar", "20x20", "30x30", "50x50"]

CAPTCHA_URL_KEYWORDS   = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
CAPTCHA_SELECTORS      = [
    "iframe[src*='recaptcha']",
    ".baxia-punish",
    "#captcha-verify",
    "[id*='captcha']",
    "iframe[src*='geetest']",
    "[class*='captcha']",
]
BLOCK_TITLE_KEYWORDS   = ["verify", "access", "denied", "blocked", "challenge"]

COMPLIANCE_TRIGGER_SELECTORS = [
    "span:has-text('Product compliance information')",
    "a:has-text('Product compliance')",
    "div:has-text('Product compliance information') >> nth=0",
    "[data-spm-anchor-id*='i30']",
]

TITLE_SELECTORS = [
    ('[data-pl="product-title"]',  "data-pl product-title"),
    ('h1[class*="title"]',         "h1 title class"),        # kept from v1
    ('[class*="product-title"]',   "product-title class"),
    ('[class*="ProductTitle"]',    "ProductTitle class"),
    ('h1',                         "h1 heading"),
]

STORE_ROW_SELECTORS = [
    "div[class*='store-detail'] table tr",
    "div[class*='storeDetail'] table tr",
    "[class*='store-detail--detail'] tr",
]

STORE_POPUP_SELECTORS = [
    "div[class*='store-detail--storePopup']",
    "div[class*='store-detail--popup']",
    "div[class*='storePopup']",
    "div[class*='store-detail']:not(a)",
]

TOR_PROXY          = "socks5://127.0.0.1:9050"
TOR_CONTROL_PORT   = 9051
TOR_WAIT_SECONDS   = 15
MAX_RETRIES        = 5
PAGE_RENDER_WAIT   = 12     # seconds after domcontentloaded
DESC_LOAD_WAIT     = 3      # seconds after clicking Description tab


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def random_delay(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """Sleep for a random duration to mimic human pacing."""
    time.sleep(random.uniform(min_s, max_s))


def clean_text(text: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fix_image_url(src: str) -> str:
    """Ensure the URL has a scheme."""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://ae01.alicdn.com" + src
    return src


def random_viewport() -> dict:
    return random.choice([
        {"width": 1366, "height": 768},
        {"width": 1920, "height": 1080},
        {"width": 1440, "height": 900},
        {"width": 1280, "height": 720},
    ])


# ---------------------------------------------------------------------------
# Tor
# ---------------------------------------------------------------------------

def rotate_tor_circuit() -> bool:
    """Signal Tor for a new exit-node circuit and wait for it to be ready."""
    try:
        with Controller.from_port(port=TOR_CONTROL_PORT) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
            print(f"   Waiting {TOR_WAIT_SECONDS}s for new Tor circuit...")
            for i in range(TOR_WAIT_SECONDS):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {TOR_WAIT_SECONDS - i - 1}s remaining")
        print("✅ Tor circuit rotated — new IP acquired")
        return True
    except Exception as exc:
        print(f"⚠️  Could not rotate Tor circuit: {exc}")
        return False


# ---------------------------------------------------------------------------
# CAPTCHA detection
# ---------------------------------------------------------------------------

def is_captcha_page(page) -> bool:
    """Return True if the current page looks like a CAPTCHA or block page."""
    page_url   = page.url.lower()
    page_title = page.title().lower()

    if any(kw in page_url for kw in CAPTCHA_URL_KEYWORDS):
        print("❌ CAPTCHA detected in URL")
        return True

    for selector in CAPTCHA_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA element detected: {selector}")
                return True
        except Exception:
            continue

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    if not is_product_page and any(kw in page_title for kw in BLOCK_TITLE_KEYWORDS):
        print("❌ Block page detected from title")
        return True

    return False


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def extract_title(page) -> str:
    """Try multiple selectors to extract the product title."""
    print("📌 Extracting title...")
    for selector, desc in TITLE_SELECTORS:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                # Must be substantial and not a breadcrumb fragment
                if title and len(title) > 20 and "/" not in title:
                    print(f"   ✅ Title ({desc}): {title[:80]}...")
                    return title
        except Exception:
            continue
    print("   ⚠️  Could not extract title")
    return ""


def extract_store_info(page) -> dict:
    """
    Extract store name from the page, then hover to trigger the detail
    popup and parse key-value rows from it.
    """
    store_info: dict = {}
    print("📦 Extracting store info...")

    try:
        # Step 1 — store name (always visible)
        elem = page.locator("span[class*='store-detail--storeName']").first
        if elem.count() > 0:
            name = elem.inner_text().strip()
            if name:
                store_info["Store Name"] = name
                print(f"   ✓ Store name: {name}")
        else:
            print("   ⚠️  Store name element not found")

        # Step 2 — hover to trigger popup
        wrap = page.locator("div[class*='store-detail--storeNameWrap']").first
        if wrap.count() > 0:
            wrap.hover()
            page.wait_for_timeout(1500)
            print("   ✓ Hovered over store element")
        else:
            print("   ⚠️  Store wrap element not found — skipping hover")

        # Step 3 — try table rows first
        for row_sel in STORE_ROW_SELECTORS:
            rows = page.locator(row_sel).all()
            if rows:
                print(f"   ✓ Found {len(rows)} rows via: {row_sel}")
                for row in rows:
                    try:
                        cols = row.locator("td").all()
                        if len(cols) >= 2:
                            key   = cols[0].inner_text().strip().rstrip(":")
                            value = cols[1].inner_text().strip()
                            if key and value:
                                store_info[key] = value
                    except Exception:
                        continue
                if len(store_info) > 1:
                    break

        # Step 4 — fallback: parse visible popup text
        if len(store_info) <= 1:
            print("   🔍 Fallback: reading popup text...")
            for popup_sel in STORE_POPUP_SELECTORS:
                popup = page.locator(popup_sel).first
                if popup.count() == 0:
                    continue
                text = popup.inner_text().strip()
                if not text:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if ":" in line:
                        key, _, value = line.partition(":")
                        key, value = key.strip(), value.strip()
                        if key and value and len(key) < 50:
                            store_info[key] = value
                if len(store_info) > 1:
                    break

        if store_info:
            print(f"   ✅ Store info: {store_info}")
        else:
            print("   ⚠️  No store info extracted")

    except Exception as exc:
        print(f"⚠️  Store extraction error: {exc}")
        traceback.print_exc()

    return store_info


def extract_compliance_info(page) -> dict:
    """
    Scroll to the bottom to reveal the compliance link, click it to open
    the modal, and parse manufacturer/EU responsible-person data from it.
    """
    compliance: dict = {}
    print("📋 Extracting compliance info...")

    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # Try each selector until the modal opens
        clicked = False
        for sel in COMPLIANCE_TRIGGER_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.count() == 0:
                    continue
                btn.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                btn.click(force=True, timeout=3000)
                page.wait_for_timeout(4000)
                if page.locator(".comet-v2-modal-body").count() > 0:
                    print(f"   ✓ Modal opened via: {sel}")
                    clicked = True
                    break
                print(f"   ⚠️  Clicked {sel} but modal did not open")
            except Exception:
                continue

        if not clicked:
            print("   ⚠️  Compliance trigger not found — skipping")
            return compliance

        modal_html = page.locator(".comet-v2-modal-body").first.inner_html(timeout=5000)

        # Work on a fresh soup so decompose() doesn't corrupt a shared object
        soup_p   = BeautifulSoup(modal_html, "html.parser")
        soup_eu  = BeautifulSoup(modal_html, "html.parser")

        # --- Parse <p> blocks ---
        for p in soup_p.find_all("p"):
            raw_html = str(p)
            strong   = p.find("strong")
            section  = strong.get_text().strip() if strong else "Info"
            section_data: dict = {}
            for line in re.split(r"<br\s*/?>", raw_html, flags=re.IGNORECASE):
                line_text = BeautifulSoup(line, "html.parser").get_text().strip()
                if ":" in line_text:
                    key, _, value = line_text.partition(":")
                    key, value = key.strip(), value.strip()
                    if key and value and len(key) < 60 and key != section:
                        section_data[key] = value
            if section_data:
                compliance[section] = section_data
                print(f"   ✓ {section}: {section_data}")

        # --- Parse EU responsible person (text outside <p> tags) ---
        for p in soup_eu.find_all("p"):
            p.decompose()   # safe — operates on its own copy
        eu_data: dict = {}
        in_eu = False
        for line in soup_eu.get_text("\n").splitlines():
            line = line.strip()
            if not line:
                continue
            if "EU responsible" in line:
                in_eu = True
                continue
            if in_eu and ":" in line:
                key, _, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if key and value and len(key) < 60:
                    eu_data[key] = value
        if eu_data:
            compliance["EU Responsible Person"] = eu_data
            print(f"   ✓ EU: {eu_data}")

        # Close modal
        try:
            page.locator(".comet-v2-modal-close").first.click(timeout=2000)
        except Exception:
            page.keyboard.press("Escape")

        print(f"   ✅ Compliance extracted: {len(compliance)} sections")

    except Exception as exc:
        print(f"   ❌ Compliance error: {exc}")

    return compliance


def extract_description(page) -> tuple[str, list[str]]:
    """
    Click the Description tab, scroll to the container, then extract text
    via three progressively deeper methods and collect image URLs.
    Returns (description_text, image_url_list).
    """
    description_text   = ""
    description_images: list[str] = []

    print("📝 Extracting description...")

    try:
        # Click the Description anchor tab
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            for btn in page.locator("a.comet-v2-anchor-link").all():
                if "description" in btn.inner_text().strip().lower():
                    btn.click(force=True, timeout=2000)
                    print("   ✓ Clicked Description tab")
                    page.wait_for_timeout(DESC_LOAD_WAIT * 1000)
                    break
        except Exception as exc:
            print(f"   ⚠️  Description tab click error: {exc}")

        # Scroll to container to trigger lazy load
        try:
            page.locator("#product-description").scroll_into_view_if_needed()
            page.wait_for_timeout(DESC_LOAD_WAIT * 1000)
        except Exception:
            pass

        # Method 0 — individual <p> tags (fastest, most precise)
        method0_text = ""
        try:
            parts = []
            for p in page.locator("#product-description p").all():
                try:
                    txt = p.inner_text(timeout=2000).strip()
                    if txt and len(txt) > 2:
                        parts.append(txt)
                except Exception:
                    pass
            if parts:
                method0_text = normalise_whitespace(" ".join(parts))
                print(f"   ✓ Method 0 (<p> tags): {len(method0_text)} chars")
            else:
                print("   ⚠️  Method 0: no <p> content found")
        except Exception as exc:
            print(f"   ⚠️  Method 0 failed: {exc}")

        desc_container = page.locator("#product-description").first
        method1_text   = ""
        method2_text   = ""

        if desc_container.count() > 0:
            print("   ✓ Found #product-description container")

            # Method 1 — inner_text() on the whole container
            try:
                method1_text = normalise_whitespace(
                    desc_container.inner_text(timeout=5000).strip()
                )
                print(f"   ✓ Method 1 (inner_text): {len(method1_text)} chars")
                if len(method1_text) < 100:
                    print("   ⏳ Short content — waiting 5s and retrying...")
                    page.wait_for_timeout(5000)
                    method1_text = normalise_whitespace(
                        desc_container.inner_text(timeout=5000).strip()
                    )
                    print(f"   ✓ Method 1 retry: {len(method1_text)} chars")
            except Exception as exc:
                print(f"   ⚠️  Method 1 failed: {exc}")

            # Method 2 — JS evaluate (handles deeply-nested / shadow-DOM divs)
            if len(method1_text) < 100:
                try:
                    js_text = page.evaluate("""
                        () => {
                            const el = document.querySelector('#product-description');
                            if (!el) return '';
                            const clone = el.cloneNode(true);
                            clone.querySelectorAll('img, script, style').forEach(n => n.remove());
                            return clone.innerText || clone.textContent || '';
                        }
                    """)
                    if js_text:
                        method2_text = normalise_whitespace(js_text)
                        print(f"   ✓ Method 2 (JS evaluate): {len(method2_text)} chars")
                    else:
                        print("   ⚠️  Method 2: empty result")
                except Exception as exc:
                    print(f"   ⚠️  Method 2 failed: {exc}")

            # Combine — deduplicate by preferring the longest non-redundant text
            texts = [t for t in [method0_text, method1_text, method2_text] if t]
            if texts:
                # Use the longest result as the canonical version; append any
                # unique content from the others that isn't already a substring.
                texts.sort(key=len, reverse=True)
                combined = texts[0]
                for extra in texts[1:]:
                    if extra and extra not in combined:
                        combined = combined + " " + extra
                description_text = normalise_whitespace(combined)
            print(f"   ✅ Final description: {len(description_text)} chars")

            # --- Image extraction ---
            all_imgs = desc_container.locator("img").all()
            print(f"   🖼️  Found {len(all_imgs)} <img> elements")
            seen: set[str] = set()
            for img in all_imgs:
                try:
                    src = None
                    for attr in ["src", "data-src", "data-lazy-src", "lazy-src", "data-orig"]:
                        src = img.get_attribute(attr)
                        if src and src.strip():
                            break
                    if not src:
                        continue
                    clean_src = fix_image_url(src.split("?")[0].split("#")[0])
                    if (
                        len(clean_src) > 40
                        and any(d in clean_src for d in VALID_IMAGE_DOMAINS)
                        and not any(b in clean_src.lower() for b in BAD_IMAGE_PATTERNS)
                        and clean_src not in seen
                    ):
                        seen.add(clean_src)
                        print(f"      ✅ {clean_src[-60:]}")
                except Exception:
                    continue

            description_images = list(seen)[:20]
            print(f"   ✅ {len(description_images)} description images collected")
        else:
            print("   ❌ #product-description container not found")

    except Exception as exc:
        print(f"⚠️  Description extraction error: {exc}")
        traceback.print_exc()

    return description_text, description_images


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_aliexpress_product(url: str) -> dict:
    """
    Scrape an AliExpress product page and return structured data.
    Retries up to MAX_RETRIES times, rotating the Tor circuit on each failure.
    """
    print(f"\n🔍 Scraping: {url}")

    empty_result = {
        "title":            "",
        "description_text": "",
        "images":           [],
        "store_info":       {},
        "compliance_info":  {},
    }

    for attempt in range(MAX_RETRIES):
        print(f"\n📍 Attempt {attempt + 1}/{MAX_RETRIES}")

        if attempt > 0:
            print("🔄 Rotating Tor circuit before retry...")
            rotate_tor_circuit()
            wait_time = 30 + (attempt * 5)
            print(f"   Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)

        # Camoufox is used as a context manager — do NOT call browser.close()
        # inside the block; __exit__ handles it automatically.
        with Camoufox(
            headless=True,
            proxy={"server": TOR_PROXY},
            geoip=True,
            locale="en-GB",
        ) as browser:

            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

            try:
                # --- Navigation ---
                target_url = url + "?gatewayAdapt=glo2usa"
                print(f"📡 Loading: {target_url}")
                page.goto(target_url, timeout=120_000, wait_until="domcontentloaded")
                time.sleep(2)

                redirected_to = page.url
                if redirected_to != target_url:
                    print(f"⚠️  Redirected to: {redirected_to}")

                if is_captcha_page(page):
                    print("⚠️  CAPTCHA on load — will retry with new circuit")
                    continue   # __exit__ closes the browser

                print(f"⏳ Waiting {PAGE_RENDER_WAIT}s for JS to render...")
                time.sleep(PAGE_RENDER_WAIT)

                # Deep scroll to trigger lazy-loaded content
                print("⏳ Deep scrolling to trigger lazy loads...")
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(3)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                    time.sleep(1)
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as exc:
                    print(f"⚠️  Scroll error: {exc}")

                if is_captcha_page(page):
                    print("⚠️  CAPTCHA after scroll — will retry with new circuit")
                    continue

                # --- Data extraction ---
                title           = extract_title(page)
                store_info      = extract_store_info(page)
                compliance_info = extract_compliance_info(page)
                description_text, description_images = extract_description(page)

                # --- Build result ---
                result = {
                    "title":            title            if isinstance(title, str)              else "",
                    "description_text": description_text if isinstance(description_text, str)   else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info       if isinstance(store_info, dict)         else {},
                    "compliance_info":  compliance_info  if isinstance(compliance_info, dict)    else {},
                }

                print("\n🔍 Summary:")
                print(f"   title:            {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images:           {len(result['images'])} items")
                print(f"   store_info keys:  {list(result['store_info'].keys())}")
                print(f"   compliance keys:  {list(result['compliance_info'].keys())}")
                print(f"✅ Extraction successful on attempt {attempt + 1}\n")
                return result

            except PlaywrightTimeoutError as exc:
                print(f"⚠️  Timeout on attempt {attempt + 1}: {exc}")
                # browser closed automatically by context manager

            except Exception as exc:
                print(f"❌ Unexpected error on attempt {attempt + 1}: {exc}")
                traceback.print_exc()
                # browser closed automatically by context manager

    print(f"❌ All {MAX_RETRIES} attempts failed")
    return empty_result
