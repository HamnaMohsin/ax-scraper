"""
AliExpress Store Item Count Scraper
====================================
Navigates to https://www.aliexpress.com/store/{store_id}/pages/all-items.html
and extracts the total number of items listed for that store.

Target element:
  <span style="font-size: 15px; font-weight: 400; color: rgb(25, 25, 25);">82 items</span>
  CSS selector: #right > div > div:nth-child(2) > span

Usage:
  python aliexpress_store_scraper.py 911431006
  python aliexpress_store_scraper.py 911431006 --headless false
  python aliexpress_store_scraper.py 911431006 --output result.json
"""

import argparse
import json
import re
import sys
import time
import random
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")

try:
    from stem import Signal
    from stem.control import Controller
except ImportError:
    sys.exit("pip install stem")


# ── Config ─────────────────────────────────────────────────────────────────────
STORE_URL_TEMPLATE = (
    "https://www.aliexpress.com/store/{store_id}/pages/all-items.html"
    "?shop_sortType=bestmatch_sort&gatewayAdapt=glo2swe"
)

# Primary selector as provided
ITEM_COUNT_SELECTOR = "#right > div > div:nth-child(2) > span"

# Fallback selectors in case the DOM structure changes
FALLBACK_SELECTORS = [
    "#right span[style*='font-size: 15px']",
    "#right div span[style*='color: rgb(25, 25, 25)']",
    ".shop-card--shopTitle--1OPn3Qu",   # some store pages use a different layout
    "span[style*='font-weight: 400'][style*='color: rgb(25, 25, 25)']",
]

MAX_CAPTCHA_ROTATIONS = 6
ROTATE_WAIT_SECS      = 14

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CAPTCHA_URL_TOKENS = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
CAPTCHA_SELECTORS  = [
    "iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
    "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']",
]


# ── Logging ────────────────────────────────────────────────────────────────────
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log(emoji: str, msg: str, indent: int = 0) -> None:
    prefix = "  " * indent
    print(f"[{ts()}] {prefix}{emoji}  {msg}", flush=True)

def log_separator(char: str = "─", width: int = 68) -> None:
    print(char * width, flush=True)


# ── Tor ────────────────────────────────────────────────────────────────────────
def rotate_tor_circuit(wait: int = ROTATE_WAIT_SECS) -> bool:
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        log("🔄", f"Tor NEWNYM sent — waiting {wait}s …", indent=1)
        time.sleep(wait)
        return True
    except Exception as e:
        log("⚠️ ", f"Tor rotation failed: {e}", indent=1)
        return False


# ── Browser helpers ────────────────────────────────────────────────────────────
def make_context(browser, headless: bool = True):
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
    )
    # Block images/fonts for faster loading
    ctx.route(
        "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2}",
        lambda r: r.abort()
    )
    return ctx

def make_page(context):
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}};
    """)
    return page

def is_captcha(page) -> bool:
    if any(t in page.url.lower() for t in CAPTCHA_URL_TOKENS):
        return True
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


# ── Item count extraction ──────────────────────────────────────────────────────
def extract_item_count(page) -> dict:
    """
    Try the primary selector first, then fallbacks.
    Returns a dict with:
        raw_text   - the full text of the matched element (e.g. "82 items")
        count      - integer count if parseable, else None
        selector   - which selector matched
    """
    # Try primary selector
    selectors_to_try = [ITEM_COUNT_SELECTOR] + FALLBACK_SELECTORS

    for selector in selectors_to_try:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                # There may be multiple matches; check each for "items" text
                for i in range(locator.count()):
                    elem = locator.nth(i)
                    text = (elem.inner_text() or "").strip()
                    if "item" in text.lower():
                        # Parse the number
                        m = re.search(r"([\d,]+)", text)
                        count = int(m.group(1).replace(",", "")) if m else None
                        return {
                            "raw_text": text,
                            "count": count,
                            "selector": selector,
                        }
        except Exception as e:
            log("⚠️ ", f"Selector '{selector}' error: {e}", indent=2)
            continue

    # Last resort: scan all spans on the page for "X items" pattern
    log("🔎", "Primary/fallback selectors failed — scanning all spans …", indent=1)
    try:
        spans = page.locator("span").all()
        for span in spans:
            try:
                text = (span.inner_text() or "").strip()
                if re.search(r"^\d[\d,]*\s+items?$", text, re.IGNORECASE):
                    m = re.search(r"([\d,]+)", text)
                    count = int(m.group(1).replace(",", "")) if m else None
                    return {
                        "raw_text": text,
                        "count": count,
                        "selector": "span (full-page scan)",
                    }
            except Exception:
                continue
    except Exception as e:
        log("⚠️ ", f"Full-page scan error: {e}", indent=2)

    return {"raw_text": None, "count": None, "selector": None}


# ── Main page loader with captcha rotation ─────────────────────────────────────
def load_store_page(browser, store_id: str, headless: bool = True):
    url = STORE_URL_TEMPLATE.format(store_id=store_id)
    log("🌐", f"Target URL: {url}")

    for attempt in range(1, MAX_CAPTCHA_ROTATIONS + 2):
        log("📡", f"Attempt {attempt}/{MAX_CAPTCHA_ROTATIONS + 1} …", indent=1)
        ctx  = make_context(browser, headless)
        page = make_page(ctx)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Give the JS a moment to render
            time.sleep(random.uniform(2.5, 4.5))

            # Try to wait for the item count element specifically
            try:
                page.wait_for_selector(ITEM_COUNT_SELECTOR, timeout=10000)
                log("✅", "Item count element found in DOM", indent=1)
            except PlaywrightTimeout:
                log("⏳", "Primary selector not visible yet — checking for captcha …", indent=1)

            if is_captcha(page):
                log("❌", "CAPTCHA detected — rotating Tor circuit …", indent=1)
                ctx.close()
                rotate_tor_circuit()
                continue

            # Scroll slightly to trigger lazy-loaded content
            page.evaluate("window.scrollBy(0, 300)")
            time.sleep(random.uniform(1.0, 2.0))

            # Extract the count
            result = extract_item_count(page)

            if result["count"] is not None:
                log("✅", f"Extracted: '{result['raw_text']}' via [{result['selector']}]", indent=1)
                ctx.close()
                return result
            else:
                log("⚠️ ", "Item count not found on page — rotating …", indent=1)
                ctx.close()
                rotate_tor_circuit()
                continue

        except Exception as exc:
            log("❌", f"Navigation error: {exc}", indent=1)
            try:
                ctx.close()
            except Exception:
                pass
            rotate_tor_circuit()

    return {"raw_text": None, "count": None, "selector": None}


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AliExpress Store Item Count Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python aliexpress_store_scraper.py 911431006
  python aliexpress_store_scraper.py 911431006 --headless false
  python aliexpress_store_scraper.py 911431006 --output result.json
        """,
    )
    parser.add_argument(
        "store_id",
        help="AliExpress store ID (e.g. 911431006)",
    )
    parser.add_argument(
        "--headless",
        default="true",
        choices=["true", "false"],
        help="Run browser in headless mode (default: true)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON file to save the result to",
    )
    args = parser.parse_args()

    headless = args.headless.lower() == "true"

    log_separator("═")
    log("🛒", f"AliExpress Store Scraper")
    log("🏪", f"Store ID : {args.store_id}")
    log("👁 ", f"Headless : {headless}")
    log_separator("═")

    result = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            extraction = load_store_page(browser, args.store_id, headless)
        finally:
            browser.close()

    result = {
        "store_id":  args.store_id,
        "store_url": STORE_URL_TEMPLATE.format(store_id=args.store_id),
        "raw_text":  extraction.get("raw_text"),
        "item_count": extraction.get("count"),
        "selector_used": extraction.get("selector"),
        "scraped_at": datetime.now().isoformat(),
    }

    log_separator("═")
    if result["item_count"] is not None:
        log("🎉", f"SUCCESS — Store {args.store_id} has {result['item_count']} items")
    else:
        log("❌", "FAILED — Could not extract item count")
    log_separator("═")

    # Pretty-print to console
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # Optionally save to file
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        log("💾", f"Result saved to {args.output}")

    return 0 if result["item_count"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
