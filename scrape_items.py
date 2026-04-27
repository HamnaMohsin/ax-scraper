"""
AliExpress Store Item Count Scraper
====================================
Extracts the total item count from an AliExpress store page.

The Baxia security system injects a modal dialog (.baxia-dialog) on top of the
normal page. The page itself loads correctly underneath — we just need to
dismiss the dialog by clicking .baxia-dialog-close, then extract the count.

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

MAX_RETRIES      = 5
ROTATE_WAIT_SECS = 14

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── Baxia dialog detection ─────────────────────────────────────────────────────
# The baxia-dialog is an OVERLAY on top of a normally loaded page.
# The page loads fine underneath — we dismiss the dialog, then scrape.
BAXIA_DIALOG_SELECTOR = ".baxia-dialog"
BAXIA_CLOSE_SELECTOR  = ".baxia-dialog-close"

# A REAL block page (not just the overlay dialog) — these mean we should rotate
HARD_BLOCK_URL_PATHS = [
    "/_____tmd_____/punish",   # full-page punish redirect (no underlying page)
    "/baxia-punish",
    "baxia.aliexpress.com",
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
    # Block images/fonts — keep JS and CSS
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


# ── Hard block check (full-page redirect, needs Tor rotation) ─────────────────
def is_hard_blocked(page) -> bool:
    """
    Returns True only when the entire page IS a block/punish page,
    meaning there is no underlying store content at all.
    The baxia-dialog overlay on top of a normal page is NOT a hard block.
    """
    url = page.url.lower()
    for path in HARD_BLOCK_URL_PATHS:
        # Only flag if the path is in the URL path component, not just any query param
        if path in url.split("?")[0]:
            log("🚫", f"Hard block URL: {path}", indent=2)
            return True

    # Check title for block pages
    try:
        title = page.title().lower()
        if any(k in title for k in ["access denied", "403 forbidden", "blocked"]):
            log("🚫", f"Hard block title: {page.title()}", indent=2)
            return True
    except Exception:
        pass

    return False


# ── Dismiss baxia-dialog overlay ──────────────────────────────────────────────
def dismiss_baxia_dialog(page) -> bool:
    """
    If the baxia security dialog is showing as an OVERLAY on top of the page,
    click the X button to close it, then wait for the underlying content.
    Returns True if a dialog was found and dismissed.
    """
    try:
        dialog = page.locator(BAXIA_DIALOG_SELECTOR)
        if dialog.count() == 0:
            return False

        # Check it's visible (style="display: block")
        style = dialog.first.get_attribute("style") or ""
        if "display: none" in style:
            return False

        log("🔔", "Baxia dialog detected — attempting to dismiss …", indent=1)

        close_btn = page.locator(BAXIA_CLOSE_SELECTOR)
        if close_btn.count() > 0:
            close_btn.first.click()
            log("✅", "Clicked .baxia-dialog-close", indent=1)
            time.sleep(random.uniform(1.5, 2.5))

            # Confirm dialog is gone
            try:
                page.wait_for_selector(
                    f"{BAXIA_DIALOG_SELECTOR}[style*='display: none']",
                    timeout=5000
                )
                log("✅", "Baxia dialog dismissed successfully", indent=1)
            except PlaywrightTimeout:
                # Dialog might still be there but hidden via class change — continue anyway
                log("⚠️ ", "Dialog close not confirmed — continuing anyway", indent=1)
            return True
        else:
            # No close button — try pressing Escape
            log("⚠️ ", "No close button found — trying Escape key", indent=1)
            page.keyboard.press("Escape")
            time.sleep(1.0)
            return True

    except Exception as e:
        log("⚠️ ", f"dismiss_baxia_dialog error: {e}", indent=2)
        return False


# ── Debug snapshot ─────────────────────────────────────────────────────────────
def snapshot(page, store_id, attempt):
    try:
        stem = f"debug_{store_id}_attempt{attempt}_{datetime.now().strftime('%H%M%S')}"
        page.screenshot(path=f"{stem}.png", full_page=False)
        log("📸", f"{stem}.png", indent=2)
        Path(f"{stem}.html").write_text(page.content(), encoding="utf-8")
        log("📄", f"{stem}.html", indent=2)
        log("🔗", f"URL  : {page.url}", indent=2)
        log("📝", f"Title: {page.title()}", indent=2)

        # Check for baxia dialog
        dialog = page.locator(BAXIA_DIALOG_SELECTOR)
        if dialog.count() > 0:
            style = dialog.first.get_attribute("style") or ""
            log("🔔", f"Baxia dialog present, style='{style}'", indent=2)
        else:
            log("✅", "No baxia dialog in DOM", indent=2)

        # Show #right content
        try:
            right_text = page.locator("#right").inner_text(timeout=3000)
            log("📦", f"#right text: {repr(right_text[:400])}", indent=2)
        except Exception:
            log("📦", "#right not found or empty", indent=2)

        # Show spans with digits
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
def wait_for_item_count_js(page, timeout_ms=20000) -> str | None:
    log("🔬", "Watching for item count …", indent=1)
    try:
        result = page.evaluate(f"""
            () => new Promise((resolve, reject) => {{
                const TIMEOUT = {timeout_ms};
                const PATTERN = /^(\\d[\\d,]*)\\s+items?$/i;

                function scan() {{
                    for (const el of document.querySelectorAll('span, div')) {{
                        const t = ((el.innerText || el.textContent) || '').trim();
                        if (PATTERN.test(t)) return t;
                    }}
                    return null;
                }}

                const existing = scan();
                if (existing) {{ resolve(existing); return; }}

                const observer = new MutationObserver(() => {{
                    const found = scan();
                    if (found) {{ observer.disconnect(); resolve(found); }}
                }});
                observer.observe(document.body, {{childList: true, subtree: true, characterData: true}});
                setTimeout(() => {{ observer.disconnect(); reject(new Error('timeout')); }}, TIMEOUT);
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
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            log("🌍", f"URL  : {page.url}", indent=1)
            log("📝", f"Title: {page.title()}", indent=1)

            # Wait for initial JS
            time.sleep(random.uniform(2.5, 4.0))

            # ── Hard block? (need Tor rotation) ───────────────────────────────
            if is_hard_blocked(page):
                log("❌", "Full page block — rotating Tor …", indent=1)
                if debug:
                    snapshot(page, store_id, attempt)
                ctx.close()
                rotate_tor()
                continue

            # ── Baxia dialog overlay? (dismiss it, page is fine underneath) ──
            baxia_visible = page.locator(f"{BAXIA_DIALOG_SELECTOR}[style*='display: block']").count() > 0
            if baxia_visible:
                dismissed = dismiss_baxia_dialog(page)
                if not dismissed:
                    log("⚠️ ", "Could not dismiss dialog — rotating Tor …", indent=1)
                    if debug:
                        snapshot(page, store_id, attempt)
                    ctx.close()
                    rotate_tor()
                    continue
            else:
                log("✅", "No blocking dialog present", indent=1)

            # ── Scroll to trigger lazy-loaded components ────────────────────
            scroll_to_load(page)

            # ── Wait for item count ────────────────────────────────────────
            raw_text = wait_for_item_count_js(page, timeout_ms=20000)

            if raw_text:
                m = re.search(r"([\d,]+)", raw_text)
                count = int(m.group(1).replace(",", "")) if m else None
                log("✅", f"Found: '{raw_text}' → {count} items", indent=1)
                if debug:
                    snapshot(page, store_id, attempt)
                ctx.close()
                return {"raw_text": raw_text, "count": count, "selector": "MutationObserver"}

            log("⚠️ ", "Item count not found this attempt", indent=1)
            if debug:
                snapshot(page, store_id, attempt)
            else:
                log("💡", "Run with --debug for screenshots + HTML", indent=1)

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
