"""
AliExpress Store Item Count Scraper
====================================
Extracts the total item count from an AliExpress store page.

Target element:
  <span style="font-size: 15px; font-weight: 400; color: rgb(25, 25, 25);">82 items</span>

Usage:
  python aliexpress_store_scraper.py 911431006
  python aliexpress_store_scraper.py 911431006 --headless false
  python aliexpress_store_scraper.py 911431006 --debug
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
    print("[WARN] stem not installed — Tor rotation disabled.", flush=True)


# ── Config ─────────────────────────────────────────────────────────────────────
STORE_URL_TEMPLATE = (
    "https://www.aliexpress.com/store/{store_id}/pages/all-items.html"
    "?shop_sortType=bestmatch_sort&gatewayAdapt=glo2swe"
)

MAX_RETRIES      = 4
ROTATE_WAIT_SECS = 14

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── REAL captcha signals only ──────────────────────────────────────────────────
# NOTE: Do NOT include iframe[src*='recaptcha'] — AliExpress loads reCAPTCHA
#       as a background trust signal on normal store pages. It is NOT a block page.
CAPTCHA_URL_PATHS = [
    "/baxia-punish",
    "/_____tmd_____/punish",
    "/punish",
    "baxia.aliexpress.com",
    "baxia-security",
]

CAPTCHA_DOM_SELECTORS = [
    ".baxia-punish",       # AliExpress block page wrapper
    "#captcha-verify",     # explicit captcha verify box
    "#nc_1_n1z",           # AliExpress slider captcha track
    ".nc-container",       # AliExpress drag-slider wrapper
    "[id^='baxia']",       # any baxia-prefixed ID
    # NOTE: "iframe[src*='geetest']" kept — GeeTest only appears on block pages
    "iframe[src*='geetest']",
    # NOTE: "iframe[src*='recaptcha']" REMOVED — present on normal store pages too
]

CAPTCHA_TITLE_KEYWORDS = [
    "captcha", "security check", "robot", "blocked", "access denied",
]


# ── Logging ────────────────────────────────────────────────────────────────────
def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(emoji, msg, indent=0):
    print(f"[{ts()}] {'  ' * indent}{emoji}  {msg}", flush=True)

def sep(char="─", width=68):
    print(char * width, flush=True)


# ── Tor ────────────────────────────────────────────────────────────────────────
def rotate_tor(wait=ROTATE_WAIT_SECS):
    if not TOR_AVAILABLE:
        return False
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        log("🔄", f"Tor NEWNYM — waiting {wait}s …", indent=1)
        time.sleep(wait)
        return True
    except Exception as e:
        log("⚠️ ", f"Tor rotation failed: {e}", indent=1)
        return False


# ── Browser ────────────────────────────────────────────────────────────────────
def make_context(browser):
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
    )
    # Block images/fonts only — keep JS and CSS intact
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,ico,woff,woff2}", lambda r: r.abort())
    return ctx

def make_page(ctx):
    page = ctx.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3]});
        window.chrome = {runtime: {}};
    """)
    return page


# ── Captcha check ──────────────────────────────────────────────────────────────
def is_captcha(page) -> bool:
    url = page.url.lower()
    for p in CAPTCHA_URL_PATHS:
        if p in url:
            log("🚫", f"Captcha URL match: {p}", indent=2)
            return True
    for sel in CAPTCHA_DOM_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                log("🚫", f"Captcha DOM match: {sel}", indent=2)
                return True
        except Exception:
            pass
    try:
        title = page.title().lower()
        if any(k in title for k in CAPTCHA_TITLE_KEYWORDS):
            log("🚫", f"Captcha page title: {page.title()}", indent=2)
            return True
    except Exception:
        pass
    return False


# ── Debug snapshot ─────────────────────────────────────────────────────────────
def snapshot(page, store_id, attempt):
    try:
        stem = f"debug_{store_id}_attempt{attempt}_{datetime.now().strftime('%H%M%S')}"
        page.screenshot(path=f"{stem}.png", full_page=False)
        log("📸", f"{stem}.png", indent=2)
        Path(f"{stem}.html").write_text(page.content(), encoding="utf-8")
        log("📄", f"{stem}.html", indent=2)
        log("🔗", f"URL: {page.url}", indent=2)
        log("📝", f"Title: {page.title()}", indent=2)

        try:
            right_text = page.locator("#right").inner_text(timeout=3000)
            log("📦", f"#right text (first 400 chars): {repr(right_text[:400])}", indent=2)
        except Exception:
            log("📦", "#right not found in DOM", indent=2)

        digit_spans = []
        for s in page.locator("span").all():
            try:
                t = (s.inner_text() or "").strip()
                if t and re.search(r"\d", t) and len(t) < 60:
                    digit_spans.append(repr(t))
            except Exception:
                pass
        log("🔢", f"Short spans with digits (first 20): {digit_spans[:20]}", indent=2)
    except Exception as e:
        log("⚠️ ", f"Snapshot failed: {e}", indent=2)


# ── Scroll to trigger lazy content ────────────────────────────────────────────
def scroll_to_load(page):
    for step in range(1, 6):
        page.evaluate(f"window.scrollTo(0, {step * 400})")
        time.sleep(0.35)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.8)


# ── MutationObserver: wait for "N items" span ─────────────────────────────────
def wait_for_item_count_js(page, timeout_ms=25000) -> str | None:
    log("🔬", "MutationObserver watching for item count span …", indent=1)
    try:
        result = page.evaluate(f"""
            () => new Promise((resolve, reject) => {{
                const TIMEOUT = {timeout_ms};
                const PATTERN = /^(\\d[\\d,]*)\\s+items?$/i;

                function scanAndResolve() {{
                    for (const el of document.querySelectorAll('span, div')) {{
                        const t = ((el.innerText || el.textContent) || '').trim();
                        if (PATTERN.test(t)) {{
                            return t;
                        }}
                    }}
                    return null;
                }}

                // Check existing DOM first
                const existing = scanAndResolve();
                if (existing) {{ resolve(existing); return; }}

                const observer = new MutationObserver(() => {{
                    const found = scanAndResolve();
                    if (found) {{
                        observer.disconnect();
                        resolve(found);
                    }}
                }});
                observer.observe(document.body, {{childList: true, subtree: true, characterData: true}});

                setTimeout(() => {{
                    observer.disconnect();
                    reject(new Error('timeout'));
                }}, TIMEOUT);
            }})
        """)
        return result
    except Exception as e:
        log("⚠️ ", f"MutationObserver: {e}", indent=1)
        return None


# ── Main loader ────────────────────────────────────────────────────────────────
def load_store_page(browser, store_id: str, debug: bool = False) -> dict:
    url = STORE_URL_TEMPLATE.format(store_id=store_id)
    log("🌐", f"Target URL: {url}")

    for attempt in range(1, MAX_RETRIES + 1):
        sep()
        log("📡", f"Attempt {attempt}/{MAX_RETRIES}")
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            # ── Navigate — use domcontentloaded (fast) ─────────────────────────
            # networkidle hangs on AliExpress due to persistent background requests
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            log("🌍", f"URL  : {page.url}", indent=1)
            log("📝", f"Title: {page.title()}", indent=1)

            # ── Short pause for initial JS hydration ───────────────────────────
            time.sleep(random.uniform(2.5, 3.5))

            # ── Captcha check ──────────────────────────────────────────────────
            if is_captcha(page):
                log("❌", "Real captcha/block page — rotating …", indent=1)
                if debug:
                    snapshot(page, store_id, attempt)
                ctx.close()
                rotate_tor()
                continue

            log("✅", "No captcha detected", indent=1)

            # ── Scroll to trigger lazy rendering ──────────────────────────────
            scroll_to_load(page)

            # ── MutationObserver wait ─────────────────────────────────────────
            raw_text = wait_for_item_count_js(page, timeout_ms=25000)

            if raw_text:
                m = re.search(r"([\d,]+)", raw_text)
                count = int(m.group(1).replace(",", "")) if m else None
                log("✅", f"Found: '{raw_text}' → {count} items", indent=1)
                if debug:
                    snapshot(page, store_id, attempt)
                ctx.close()
                return {"raw_text": raw_text, "count": count, "selector": "MutationObserver/span+div scan"}

            # ── Not found ──────────────────────────────────────────────────────
            log("⚠️ ", "Item count not found this attempt", indent=1)
            if debug:
                snapshot(page, store_id, attempt)
            else:
                log("💡", "Run with --debug for screenshots + HTML dumps", indent=1)

            ctx.close()
            time.sleep(random.uniform(3, 5))

        except Exception as exc:
            log("❌", f"Error: {exc}", indent=1)
            try:
                if debug:
                    snapshot(page, store_id, attempt)
                ctx.close()
            except Exception:
                pass
            rotate_tor()

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
    parser.add_argument("store_id",   help="AliExpress store ID (e.g. 911431006)")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--output",   default=None, help="Save JSON result to file")
    parser.add_argument("--debug",    action="store_true",
                        help="Save screenshot + HTML dump per attempt")
    args = parser.parse_args()

    headless = args.headless.lower() == "true"

    sep("═")
    log("🛒", "AliExpress Store Item Count Scraper")
    log("🏪", f"Store ID : {args.store_id}")
    log("👁 ", f"Headless : {headless}")
    log("🐛", f"Debug    : {args.debug}")
    sep("═")

    launch_kwargs = {
        "headless": headless,
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }
    if TOR_AVAILABLE:
        launch_kwargs["proxy"] = {"server": "socks5://127.0.0.1:9050"}

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

    sep("═")
    if result["item_count"] is not None:
        log("🎉", f"RESULT — Store {args.store_id} has {result['item_count']} items")
    else:
        log("❌", "FAILED — Could not extract item count")
        log("💡", "Try: --debug --headless false", indent=1)
    sep("═")

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log("💾", f"Saved to {args.output}")

    return 0 if result["item_count"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
