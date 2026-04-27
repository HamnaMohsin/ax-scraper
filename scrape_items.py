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
  python aliexpress_store_scraper.py 911431006 --debug        # saves screenshots + HTML
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
    TOR_AVAILABLE = True
except ImportError:
    TOR_AVAILABLE = False
    print("[WARN] stem not installed — Tor rotation disabled. pip install stem", flush=True)


# ── Config ─────────────────────────────────────────────────────────────────────
STORE_URL_TEMPLATE = (
    "https://www.aliexpress.com/store/{store_id}/pages/all-items.html"
    "?shop_sortType=bestmatch_sort&gatewayAdapt=glo2swe"
)

ITEM_COUNT_SELECTOR = "#right > div > div:nth-child(2) > span"

FALLBACK_SELECTORS = [
    "#right span[style*='font-size: 15px']",
    "#right div span[style*='color: rgb(25, 25, 25)']",
    "span[style*='font-weight: 400'][style*='color: rgb(25, 25, 25)']",
    "#right span",
]

MAX_RETRIES      = 5
ROTATE_WAIT_SECS = 14

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── TIGHTENED captcha signals ──────────────────────────────────────────────────
# Only definitive block/captcha URL patterns — NOT generic tokens like "verify"
CAPTCHA_URL_PATHS = [
    "/baxia-punish",
    "/_____tmd_____/punish",
    "/punish",
    "baxia.aliexpress.com",
    "baxia-security",
]

CAPTCHA_DOM_SELECTORS = [
    ".baxia-punish",
    "#captcha-verify",
    "iframe[src*='geetest']",
    "iframe[src*='recaptcha']",
    "#nc_1_n1z",        # AliExpress slider captcha
    ".nc-container",    # AliExpress drag-slider container
    "[id^='baxia']",
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
    if not TOR_AVAILABLE:
        log("⚠️ ", "Tor not available — skipping rotation", indent=1)
        return False
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
def make_context(browser):
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
    )
    ctx.route(
        "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2}",
        lambda r: r.abort()
    )
    return ctx

def make_page(context):
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        window.chrome = {runtime: {}};
    """)
    return page


# ── Captcha detection (tightened) ─────────────────────────────────────────────
def is_captcha(page, debug: bool = False) -> bool:
    current_url = page.url.lower()

    for path in CAPTCHA_URL_PATHS:
        if path in current_url:
            log("🚫", f"Captcha URL match: '{path}'", indent=2)
            return True

    for sel in CAPTCHA_DOM_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                log("🚫", f"Captcha DOM match: '{sel}'", indent=2)
                return True
        except Exception:
            pass

    try:
        title = page.title().lower()
        if any(kw in title for kw in ["captcha", "security check", "robot", "blocked", "access denied"]):
            log("🚫", f"Captcha page title: '{page.title()}'", indent=2)
            return True
    except Exception:
        pass

    if debug:
        log("✅", f"No captcha detected. URL: {page.url}", indent=2)

    return False


# ── Debug snapshot ─────────────────────────────────────────────────────────────
def save_debug_snapshot(page, store_id: str, attempt: int) -> None:
    try:
        ts_str = datetime.now().strftime("%H%M%S")
        stem   = f"debug_{store_id}_attempt{attempt}_{ts_str}"

        shot_path = f"{stem}.png"
        page.screenshot(path=shot_path, full_page=False)
        log("📸", f"Screenshot: {shot_path}", indent=2)

        html_path = f"{stem}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        log("📄", f"HTML dump : {html_path}", indent=2)

        log("🔗", f"URL  : {page.url}", indent=2)
        log("📝", f"Title: {page.title()}", indent=2)

        # Show any spans containing "item"
        item_spans = []
        for s in page.locator("span").all():
            try:
                txt = (s.inner_text() or "").strip()
                if txt and "item" in txt.lower():
                    item_spans.append(repr(txt))
            except Exception:
                pass
        if item_spans:
            log("🔍", f"Spans with 'item': {item_spans}", indent=2)
        else:
            log("🔍", "No spans containing 'item' found", indent=2)

    except Exception as e:
        log("⚠️ ", f"Debug snapshot failed: {e}", indent=2)


# ── Item count extraction ──────────────────────────────────────────────────────
def extract_item_count(page) -> dict:
    selectors_to_try = [ITEM_COUNT_SELECTOR] + FALLBACK_SELECTORS

    for selector in selectors_to_try:
        try:
            locator = page.locator(selector)
            n = locator.count()
            if n == 0:
                continue
            log("🔎", f"Selector '{selector}' matched {n} element(s)", indent=2)
            for i in range(n):
                try:
                    text = (locator.nth(i).inner_text() or "").strip()
                    log("🔎", f"  [{i}] text='{text}'", indent=2)
                    if re.search(r"\d", text) and "item" in text.lower():
                        m = re.search(r"([\d,]+)", text)
                        count = int(m.group(1).replace(",", "")) if m else None
                        return {"raw_text": text, "count": count, "selector": selector}
                except Exception:
                    continue
        except Exception as e:
            log("⚠️ ", f"Selector '{selector}' error: {e}", indent=2)

    # Full-page span scan
    log("🔎", "Scanning ALL spans for 'N items' pattern …", indent=2)
    try:
        for span in page.locator("span").all():
            try:
                text = (span.inner_text() or "").strip()
                if re.search(r"^\d[\d,]*\s+items?$", text, re.IGNORECASE):
                    m = re.search(r"([\d,]+)", text)
                    count = int(m.group(1).replace(",", "")) if m else None
                    return {"raw_text": text, "count": count, "selector": "span (full-page scan)"}
            except Exception:
                continue
    except Exception as e:
        log("⚠️ ", f"Full-page scan error: {e}", indent=2)

    return {"raw_text": None, "count": None, "selector": None}


# ── Main loader ────────────────────────────────────────────────────────────────
def load_store_page(browser, store_id: str, debug: bool = False) -> dict:
    url = STORE_URL_TEMPLATE.format(store_id=store_id)
    log("🌐", f"Target URL: {url}")

    for attempt in range(1, MAX_RETRIES + 1):
        log_separator()
        log("📡", f"Attempt {attempt}/{MAX_RETRIES}")
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            log("🌍", f"Loaded. URL  : {page.url}", indent=1)
            log("📝", f"         Title: {page.title()}", indent=1)

            # Let JS hydrate
            time.sleep(random.uniform(3, 5))

            # Check for real captcha
            if is_captcha(page, debug=True):
                log("❌", "Captcha/block page — rotating …", indent=1)
                if debug:
                    save_debug_snapshot(page, store_id, attempt)
                ctx.close()
                rotate_tor_circuit()
                continue

            # Try to wait for item count element
            log("⏳", f"Waiting for: {ITEM_COUNT_SELECTOR}", indent=1)
            try:
                page.wait_for_selector(ITEM_COUNT_SELECTOR, timeout=12000)
                log("✅", "Item count element found!", indent=1)
            except PlaywrightTimeout:
                log("⚠️ ", "Primary selector timed out — will try fallbacks", indent=1)

            # Scroll to trigger lazy content
            page.evaluate("window.scrollBy(0, 400)")
            time.sleep(random.uniform(1.0, 2.0))

            if debug:
                save_debug_snapshot(page, store_id, attempt)

            result = extract_item_count(page)

            if result["count"] is not None:
                log("✅", f"Found: '{result['raw_text']}' via [{result['selector']}]", indent=1)
                ctx.close()
                return result
            else:
                log("⚠️ ", "Count not found — retrying …", indent=1)
                if not debug:
                    log("💡", "Run with --debug to save screenshots + HTML", indent=1)
                ctx.close()
                time.sleep(random.uniform(3, 6))
                continue

        except Exception as exc:
            log("❌", f"Error: {exc}", indent=1)
            try:
                if debug:
                    save_debug_snapshot(page, store_id, attempt)
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
  python aliexpress_store_scraper.py 911431006 --debug
  python aliexpress_store_scraper.py 911431006 --output result.json
        """,
    )
    parser.add_argument("store_id", help="AliExpress store ID (e.g. 911431006)")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--output", default=None, help="Save JSON result to this file")
    parser.add_argument("--debug", action="store_true",
                        help="Save screenshots + HTML for every attempt")
    args = parser.parse_args()

    headless = args.headless.lower() == "true"

    log_separator("═")
    log("🛒", "AliExpress Store Item Count Scraper")
    log("🏪", f"Store ID : {args.store_id}")
    log("👁 ", f"Headless : {headless}")
    log("🐛", f"Debug    : {args.debug}")
    log_separator("═")

    launch_kwargs = {
        "headless": headless,
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }
    if TOR_AVAILABLE:
        launch_kwargs["proxy"] = {"server": "socks5://127.0.0.1:9050"}
    else:
        log("⚠️ ", "Running WITHOUT Tor proxy")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        try:
            extraction = load_store_page(browser, args.store_id, debug=args.debug)
        finally:
            browser.close()

    result = {
        "store_id":      args.store_id,
        "store_url":     STORE_URL_TEMPLATE.format(store_id=args.store_id),
        "raw_text":      extraction.get("raw_text"),
        "item_count":    extraction.get("count"),
        "selector_used": extraction.get("selector"),
        "scraped_at":    datetime.now().isoformat(),
    }

    log_separator("═")
    if result["item_count"] is not None:
        log("🎉", f"RESULT — Store {args.store_id} has {result['item_count']} items")
    else:
        log("❌", "FAILED — Could not extract item count")
        log("💡", "Run with --debug to save screenshots + HTML dumps", indent=1)
    log_separator("═")

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log("💾", f"Saved to {args.output}")

    return 0 if result["item_count"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
