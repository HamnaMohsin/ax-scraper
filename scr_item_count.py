"""
AliExpress Store Item Count Scraper — Tor-enabled v3
=====================================================
Key changes vs v2:
  - Uses camoufox (anti-detection Firefox) instead of raw Chromium
    → bypasses canvas/WebGL/timing fingerprinting that Baxia checks
    → install: pip install camoufox[geoip] && python -m camoufox fetch
  - Falls back to playwright-stealth Chromium if camoufox unavailable
  - API fallback now checks response content-type and prints raw text on failure
  - Added 3 alternative API endpoints tried in order
  - Baxia: after MAX_BAXIA_CYCLES failed self-dismiss cycles, skip the attempt
    immediately (stop wasting 75s polling when the IP is flagged)
  - Initial modal check happens BEFORE polling starts (not only inside poll loop)
  - Configurable headless mode (HEADLESS=0 python ... for visible browser)
  - "Something went wrong" page detection: captures exact error text from page
  - Debug screenshot saved as {store_id}.png ONLY after item count or error is found
  - Silent redirect detection: bails immediately if page redirected away from store
  - Reduced polling timeout from 60s to 20s to cut losses on bad pages

Requirements:
    pip install camoufox[geoip] playwright stem
    python -m camoufox fetch        # downloads Firefox binary
    playwright install chromium     # fallback only

    Tor must be running:
      sudo apt install tor
      /etc/tor/torrc:
        ControlPort 9051
        CookieAuthentication 0
      sudo systemctl restart tor

Usage:
    python aliexpress_scraper.py 1764075
    HEADLESS=0 python aliexpress_scraper.py 1764075   # visible browser
"""

from datetime import datetime
import sys
import os
import re
import time
import random
import json
import csv
from stem import Signal
from stem.control import Controller

# ── Try camoufox first, fall back to plain playwright ─────────────────────────
try:
    from camoufox.sync_api import Camoufox
    USE_CAMOUFOX = True
    print("✅ Using camoufox (anti-detection Firefox)")
except ImportError:
    from playwright.sync_api import sync_playwright
    USE_CAMOUFOX = False
    print("⚠️  camoufox not installed — using playwright Chromium (less stealthy)")
    print("    Install: pip install camoufox[geoip] && python -m camoufox fetch\n")

from playwright.sync_api import TimeoutError as PlaywrightTimeout


# ── Config ────────────────────────────────────────────────────────────────────

HEADLESS         = os.environ.get("HEADLESS", "1") != "0"
MAX_ATTEMPTS     = 3
ROTATE_WAIT_SECS = 14
MAX_BAXIA_CYCLES = 1   # give up on attempt if Baxia persists this many cycles

# Directory where debug screenshots are saved
SCREENSHOT_DIR = os.environ.get("SCREENSHOT_DIR", "screenshots")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

HARD_CAPTCHA_URL_TOKENS = ["baxia-security", "punish", "_____tmd_____"]

ITEM_COUNT_PATTERNS = [
    re.compile(r"([\d,]+)\s*items?",    re.IGNORECASE),
    re.compile(r"([\d,]+)\s*products?", re.IGNORECASE),
    re.compile(r"共\s*([\d,]+)\s*件"),
]

ITEM_COUNT_SELECTORS = [
    "#right > div > div:nth-child(2) > span",
    "[class*='store-detail'] span[class*='count']",
    "[class*='totalNum']",
    "[class*='total-num']",
    "[class*='itemCount']",
    "[class*='item-count']",
    "[class*='goods-count']",
    "[class*='product-count']",
]

# API endpoints to try in order
API_ENDPOINTS = [
    "https://www.aliexpress.com/store/async/search.do?storeId={sid}&SortType=bestmatch_sort&page=1&pageSize=20&origin=n&isOverseas=false&countryCode=SE",
    "https://aliexpress.com/store/productgroupsearch/endpoint.htm?storeId={sid}&sortType=bestmatch_sort&page=1&pageSize=20&countryCode=SE",
]

# Patterns that indicate a "something went wrong" / error page
PAGE_ERROR_PATTERNS = [
    re.compile(r"something\s+went\s+wrong",           re.IGNORECASE),
    re.compile(r"page\s+not\s+found",                 re.IGNORECASE),
    re.compile(r"404",                                re.IGNORECASE),
    re.compile(r"store\s+(has\s+been\s+)?closed",     re.IGNORECASE),
    re.compile(r"this\s+store\s+is\s+no\s+longer",   re.IGNORECASE),
    re.compile(r"service\s+unavailable",              re.IGNORECASE),
    re.compile(r"access\s+denied",                    re.IGNORECASE),
    re.compile(r"error\s+occurred",                   re.IGNORECASE),
]

# CSS selectors likely to contain the error message text
PAGE_ERROR_SELECTORS = [
    "[class*='error-page']",
    "[class*='error-content']",
    "[class*='error-title']",
    "[class*='not-found']",
    "[class*='404']",
    "[class*='wrong']",
    "[class*='oops']",
    "h1",
    "h2",
    ".error",
    "#error",
]


def load_store_ids_from_csv(file_path: str) -> list[str]:
    store_ids = []

    with open(file_path, newline='', encoding="latin-1") as f:
        reader = csv.DictReader(f)

        for row in reader:
            sid = row.get("MerchantID")  # ✅ correct column name

            if sid:
                store_ids.append(sid.strip())

    print(f"✅ Loaded {len(store_ids)} store IDs")
    return store_ids


# ── Screenshot helper ─────────────────────────────────────────────────────────

def save_debug_screenshot(page, store_id: str) -> str | None:
    """
    Save a PNG screenshot named {store_id}.png into SCREENSHOT_DIR.
    Called ONLY once per store — after the final result (count or error) is known.
    Returns the file path on success, None on failure.
    """
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"{store_id}.png")
        page.screenshot(path=path, full_page=False)
        print(f"   📸 Screenshot saved: {path}")
        return path
    except Exception as e:
        print(f"   ⚠️  Screenshot failed: {e}")
        return None


# ── Page error detection ──────────────────────────────────────────────────────

def detect_page_error(page) -> str | None:
    """
    Check whether the current page is an error/wrong page.

    Strategy:
      1. Try each PAGE_ERROR_SELECTORS to find a visible element whose text
         matches one of the PAGE_ERROR_PATTERNS — return its exact trimmed text.
      2. If no selector matched, scan the full body text for any pattern —
         return the matching sentence/fragment (up to 200 chars).
      3. Return None if the page looks normal.
    """
    # Step 1: targeted selector scan
    for sel in PAGE_ERROR_SELECTORS:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(min(count, 5)):  # check up to 5 matching elements
                try:
                    text = loc.nth(i).text_content(timeout=1_500)
                    if not text:
                        continue
                    text = text.strip()
                    if len(text) > 500:
                        text = text[:500]
                    for pat in PAGE_ERROR_PATTERNS:
                        if pat.search(text):
                            # Return the most focused fragment we can find
                            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                            for line in lines:
                                if pat.search(line):
                                    return line[:300]
                            return text[:300]
                except Exception:
                    continue
        except Exception:
            continue

    # Step 2: full body text scan
    try:
        body_text = page.evaluate("() => document.body.innerText")
        if body_text:
            for pat in PAGE_ERROR_PATTERNS:
                m = pat.search(body_text)
                if m:
                    # Return the surrounding sentence (up to 200 chars centred on match)
                    start = max(0, m.start() - 60)
                    end   = min(len(body_text), m.end() + 140)
                    fragment = body_text[start:end].strip().replace("\n", " ")
                    return fragment[:300]
    except Exception:
        pass

    return None


# ── Silent redirect detection ─────────────────────────────────────────────────

def detect_silent_redirect(page, store_id: str) -> str | None:
    """
    Returns an error string if the page redirected away from the expected store URL.
    Catches cases where AliExpress silently sends the browser elsewhere,
    which would otherwise cause the scraper to burn the full polling timeout
    searching for item count selectors that will never appear.
    """
    current = page.url.lower()

    # Redirected away from store pages entirely
    if "/store/" not in current:
        return f"Redirected away from store: {page.url}"

    # Landed on a different store's page
    if f"/store/{store_id}" not in current and f"/store/{store_id.lower()}" not in current:
        return f"Redirected to wrong store: {page.url}"

    # Landed on homepage or search
    if any(x in current for x in ["aliexpress.com/wholesale", "aliexpress.com/?", "aliexpress.com/w/"]):
        return f"Redirected to search/home: {page.url}"

    return None


# ── Tor ───────────────────────────────────────────────────────────────────────

def rotate_tor_circuit(wait: int = ROTATE_WAIT_SECS) -> bool:
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        print(f"   🔄 Tor NEWNYM — waiting {wait}s...")
        for i in range(wait):
            time.sleep(1)
            if i % 5 == 4:
                print(f"      ... {wait - i - 1}s remaining")
        print("   ✅ Circuit rotated")
        return True
    except Exception as e:
        print(f"   ⚠️  Tor rotation failed: {e}")
        return False

# Selectors for the small floating captcha tab with ✕ close button
_SMALL_CAPTCHA_CLOSE_SELECTORS = [
    "[class*='captcha'] [class*='close']",
    "[class*='captcha'] [class*='btn-close']",
    "[class*='captcha-close']",
    "[class*='nc_iconfont']",          # AliExpress slider captcha close icon
    "[class*='slideWrap'] [class*='close']",
    "button[class*='close'][style*='position']",   # floating close buttons
    "[class*='dialog'] button[class*='close']",
    "[aria-label='Close'][class*='captcha']",
]

def _try_close_small_captcha(page) -> bool:
    """
    Silently attempt to close the small floating captcha tab (✕ button).
    Returns True if something was clicked, False otherwise.
    Does NOT block or wait — fire-and-forget.
    """
    for sel in _SMALL_CAPTCHA_CLOSE_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                print(f"   🔒 Closed small captcha tab via: {sel}")
                time.sleep(1)
                return True
        except Exception:
            continue
    return False


# ── Browser launch / teardown ─────────────────────────────────────────────────

def launch_browser_and_page(store_id: str):
    """Returns (cm, browser, ctx, page). Works for both camoufox and playwright."""
    cookies = [
    # Primary ship-to cookie — region=SE is the key field
    {"name": "aep_usuc_f",  "value": "site=glo&c_tp=SEK&region=SE&b_locale=en_US",
     "domain": ".aliexpress.com", "path": "/"},
    # Secondary locale cookie — x_site=SWE
    {"name": "xman_us_f",   "value": "x_locale=en_US&x_site=SWE",
     "domain": ".aliexpress.com", "path": "/"},
    # These two are what the ship-to selector actually writes in the browser
    {"name": "aep_common_f",          "value": "F=F&reg=SE",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "_aep_modified_region",  "value": "SE",
     "domain": ".aliexpress.com", "path": "/"},
]

    if USE_CAMOUFOX:
        cf = Camoufox(
            headless=HEADLESS,
            proxy={"server": "socks5://127.0.0.1:9050"},
            geoip=True,      # spoof geo to match Tor exit node
            humanize=True,   # human-like timing and mouse
        )
        browser = cf.__enter__()
        ctx = browser.new_context(
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        return cf, browser, ctx, page

    else:
        pw = sync_playwright().__enter__()
        browser = pw.chromium.launch(
            headless=HEADLESS,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        return pw, browser, ctx, page


def close_all(cm, browser, ctx):
    for obj in (ctx, browser, cm):
        try:
            if hasattr(obj, "close"):
                obj.close()
            elif hasattr(obj, "__exit__"):
                obj.__exit__(None, None, None)
        except Exception:
            pass


# ── CAPTCHA / Baxia handling ──────────────────────────────────────────────────

def is_hard_captcha(page) -> bool:
    if any(t in page.url.lower() for t in HARD_CAPTCHA_URL_TOKENS):
        print(f"   ❌ Hard block URL: {page.url}")
        return True
    return False


def has_baxia_modal(page) -> bool:
    try:
        for sel in ["[class*='baxia-dialog']", "[class*='baxia_dialog']", "[id*='baxia']"]:
            if page.locator(sel).count() > 0:
                return True
        if page.locator("text=check if you are a robot").count() > 0:
            return True
        return False
    except Exception:
        return False


CLOSE_SELECTORS = [
    "[class*='baxia-dialog-close']",
    "[class*='dialog-close']",
    "button.baxia-dialog-close",
    "[aria-label='Close']",
    "[aria-label='close']",
    "button[class*='close']",
    "[class*='modal-close']",
]


def force_close_modal(page) -> bool:
    for sel in CLOSE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                time.sleep(1)
                if not has_baxia_modal(page):
                    print(f"   ✅ Modal closed via {sel}")
                    return True
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        time.sleep(1)
        if not has_baxia_modal(page):
            return True
    except Exception:
        pass
    print("   ❌ Could not close modal")
    return False


def handle_baxia_once(page, url: str) -> bool:
    """
    One full Baxia cycle:
      1. Wait 25s for JS challenge to self-complete
      2. Force close if it didn't
      3. Reload the page (mandatory — content won't appear after a forced close)
      4. Wait 15s more for any post-reload modal
    Returns True if modal is gone.
    """
    # Step 1: wait for self-dismiss
    print("   ⏳ Waiting 25s for Baxia to self-dismiss...")
    for _ in range(25):
        if not has_baxia_modal(page):
            print("   ✅ Baxia self-dismissed — session clean")
            time.sleep(1)
            return True
        time.sleep(1)

    # Step 2: force close
    print("   ⚠️  Timed out — force closing...")
    force_close_modal(page)
    time.sleep(1)

    # Step 3: reload (content won't render after forced close without this)
    print("   🔄 Reloading page after forced close...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(4)
    except Exception as e:
        print(f"   ⚠️  Reload failed: {e}")
        return False

    if not has_baxia_modal(page):
        return True

    # Step 4: short wait after reload
    print("   ⏳ Waiting 15s for post-reload self-dismiss...")
    for _ in range(15):
        if not has_baxia_modal(page):
            return True
        time.sleep(1)

    return False


# ── Item count helpers ────────────────────────────────────────────────────────

def extract_count(text: str) -> int | None:
    if not text:
        return None
    for pat in ITEM_COUNT_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def try_css_selectors(page) -> str | None:
    for sel in ITEM_COUNT_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                text = loc.first.text_content(timeout=2_000)
                if text and extract_count(text) is not None:
                    return text.strip()
        except Exception:
            continue
    return None


def try_span_scan(page) -> str | None:
    try:
        return page.evaluate(r"""() => {
            const pats = [
                /\d[\d,]*\s*items?/i,
                /\d[\d,]*\s*products?/i,
                /共\s*\d[\d,]*\s*件/,
            ];
            for (const el of document.querySelectorAll('span,div,p,h1,h2,h3,b,strong')) {
                const t = el.textContent.trim();
                if (t.length > 80) continue;
                for (const p of pats) {
                    if (p.test(t)) return t;
                }
            }
            return null;
        }""")
    except Exception:
        return None


def try_api_fallback(page, store_id: str) -> int | None:
    """Try multiple API endpoints. Logs response preview on non-JSON replies."""
    for tpl in API_ENDPOINTS:
        api_url = tpl.format(sid=store_id)
        print(f"   🌐 API: {api_url}")
        try:
            resp = page.request.get(
                api_url,
                timeout=20_000,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://www.aliexpress.com/store/{store_id}/pages/all-items.html"
                            f"?shop_sortType=bestmatch_sort&gatewayAdapt=glo2swe",
                },
            )
            raw = resp.text()
            stripped = raw.strip()

            if not stripped.startswith("{") and not stripped.startswith("["):
                preview = stripped[:200].replace("\n", " ")
                print(f"   ⚠️  Non-JSON ({resp.status}): {preview}")
                continue

            data = json.loads(stripped)
            for path in [
                ["result", "totalCount"],
                ["result", "resultCount"],
                ["result", "count"],
                ["data", "totalCount"],
                ["totalCount"],
                ["count"],
            ]:
                val = data
                try:
                    for key in path:
                        val = val[key]
                    if isinstance(val, int) and val >= 0:
                        print(f"   ✅ API count={val:,} via {'.'.join(str(k) for k in path)}")
                        return val
                except (KeyError, TypeError):
                    continue

            top_keys = list(data.keys()) if isinstance(data, dict) else type(data)
            print(f"   ⚠️  JSON OK but no count found. Keys: {top_keys}")

        except Exception as e:
            print(f"   ⚠️  API error: {e}")

    return None


def scroll_gently(page):
    for _ in range(4):
        page.mouse.wheel(0, random.randint(250, 450))
        time.sleep(random.uniform(0.4, 0.8))
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.5)


# ── Core scraper ──────────────────────────────────────────────────────────────

def scrape_store_item_count(store_id: str) -> dict:
    url = (
        f"https://www.aliexpress.com/store/{store_id}/pages/all-items.html"
        f"?shop_sortType=bestmatch_sort&gatewayAdapt=glo2swe"
    )

    print(f"\n🛒  AliExpress Store Scraper (Tor + camoufox) v3")
    print("━" * 52)
    print(f"📦  Store ID : {store_id}")
    print(f"🔗  URL      : {url}")
    print(f"🕵️  Headless  : {HEADLESS}")
    print(f"🦊  Engine   : {'camoufox/Firefox' if USE_CAMOUFOX else 'playwright/Chromium'}")
    print("━" * 52 + "\n")

    empty = {"store_id": store_id, "url": url, "item_count_text": None, "item_count": None}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n📍 Attempt {attempt}/{MAX_ATTEMPTS}")

        if attempt > 1:
            rotate_tor_circuit(wait=ROTATE_WAIT_SECS + attempt * 2)

        cm, browser, ctx, page = launch_browser_and_page(store_id)

        try:
            # ── Navigate ──────────────────────────────────────────────────────
            print("   ⏳ Navigating...")
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            time.sleep(random.uniform(2, 4))

            if is_hard_captcha(page):
                close_all(cm, browser, ctx)
                continue

            # ── Silent redirect check ─────────────────────────────────────────
            redirect_error = detect_silent_redirect(page, store_id)
            if redirect_error:
                print(f"   ❌ Silent redirect: {redirect_error}")
                save_debug_screenshot(page, store_id)
                close_all(cm, browser, ctx)
                result = {
                    "store_id":        store_id,
                    "url":             url,
                    "item_count_text": None,
                    "item_count":      None,
                    "error":           redirect_error,
                    "source":          "redirect",
                }
                _print_result(result)
                return result

            # ── Early page error check (before Baxia handling) ────────────────
            page_error = detect_page_error(page)
            if page_error:
                print(f"   ❌ Page error detected: '{page_error}'")
                save_debug_screenshot(page, store_id)
                close_all(cm, browser, ctx)
                result = {
                    "store_id":        store_id,
                    "url":             url,
                    "item_count_text": None,
                    "item_count":      None,
                    "error":           page_error,
                    "source":          "page_error",
                }
                _print_result(result)
                return result

            # ── Clear Baxia BEFORE polling ────────────────────────────────────
            baxia_cycles = 0
            while has_baxia_modal(page) and baxia_cycles < MAX_BAXIA_CYCLES:
                print(f"   ⚠️  Baxia modal (cycle {baxia_cycles + 1}/{MAX_BAXIA_CYCLES})")
                cleared = handle_baxia_once(page, url)
                baxia_cycles += 1
                if not cleared:
                    break

            if has_baxia_modal(page):
                print(f"   ❌ Baxia stuck after {baxia_cycles} cycle(s) — this IP is flagged, rotating...")
                close_all(cm, browser, ctx)
                continue

            # ── Network error modal ───────────────────────────────────────────
            try:
                btn = page.locator("text=Network error, click to reload").locator("..")
                btn.wait_for(timeout=3_000)
                print("   ⚠️  Network error modal — reloading...")
                btn.click()
                time.sleep(3)
            except Exception:
                pass

            scroll_gently(page)

            # ── Wait for real data to load (CRITICAL FIX) ───────────────────────
            print("   ⏳ Waiting for store data to hydrate...")

            try:
                page.wait_for_function(
                    """() => {
                        const text = document.body.innerText;
                        return /\\d[\\d,]*\\s*(items|products)/i.test(text) && !/\\b0\\s*(items|products)\\b/i.test(text);
                    }""",
                    timeout=20000
                )
                print("   ✅ Data hydrated (non-zero count detected)")
            except Exception:
                print("   ⚠️ Hydration wait timeout — continuing anyway")

            # ── Post-hydration page error check ──────────────────────────────
            # Re-check after JS finishes loading in case the error rendered late
            page_error = detect_page_error(page)
            if page_error:
                print(f"   ❌ Page error after hydration: '{page_error}'")
                save_debug_screenshot(page, store_id)
                close_all(cm, browser, ctx)
                result = {
                    "store_id":        store_id,
                    "url":             url,
                    "item_count_text": None,
                    "item_count":      None,
                    "error":           page_error,
                    "source":          "page_error",
                }
                _print_result(result)
                return result

            # ── Poll for item count (20s max — reduced from 60s) ──────────────
            print("   ⏳ Polling for item count (up to 20s)...")
            deadline  = time.time() + 20          # ← reduced from 60s to 20s
            check_at  = time.time() + 10
            item_count_text = None

            while time.time() < deadline:
                if time.time() >= check_at:
                    if has_baxia_modal(page):
                        print("   ⚠️  Baxia reappeared in polling — one cycle then abort...")
                        cleared = handle_baxia_once(page, url)
                        if not cleared:
                            print("   ❌ Baxia stuck — aborting poll")
                            break
                        scroll_gently(page)
                    check_at = time.time() + 10

                # ── Try to dismiss small captcha tab (✕ button) ───────────────
                _try_close_small_captcha(page)

                text = try_css_selectors(page) or try_span_scan(page)
                if text:
                    count = extract_count(text)
                    if count == 0:
                        print(f"   ⚠️  Got '0 items' — likely captcha overlay still active, retrying...")
                        time.sleep(3)
                        continue   # keep polling, never accept 0
                    if count is not None:
                        item_count_text = text
                        print(f"   ✅ Found: '{text}'")
                        break
                time.sleep(2)

            if not item_count_text:
                api_count = try_api_fallback(page, store_id)
                if api_count is not None:
                    # ── Screenshot taken HERE — after final result known ───────
                    save_debug_screenshot(page, store_id)
                    close_all(cm, browser, ctx)
                    result = {
                        "store_id":        store_id,
                        "url":             url,
                        "item_count_text": f"{api_count:,} items (API)",
                        "item_count":      api_count,
                        "source":          "api",
                    }
                    _print_result(result)
                    return result

                print("   ❌ Nothing found — rotating...")
                close_all(cm, browser, ctx)
                continue

            item_count = extract_count(item_count_text)

            # ── Screenshot taken HERE — after final result known ──────────────
            save_debug_screenshot(page, store_id)
            close_all(cm, browser, ctx)

            result = {
                "store_id":        store_id,
                "url":             url,
                "item_count_text": item_count_text,
                "item_count":      item_count,
                "source":          "dom",
            }
            _print_result(result)
            return result

        except PlaywrightTimeout as e:
            print(f"   ⚠️  Timeout: {e}")
            close_all(cm, browser, ctx)

        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback; traceback.print_exc()
            close_all(cm, browser, ctx)

    print(f"\n❌  Failed after {MAX_ATTEMPTS} attempts")
    return empty


def _print_result(r: dict):
    print(f"\n✅  Result")
    print("━" * 52)
    if r.get("error"):
        print(f"⚠️   Page Error : \"{r['error']}\"")
    else:
        print(f"🏷️   Raw Text  : \"{r['item_count_text']}\"")
        print(f"🔢  Count      : {r['item_count']:,}" if r["item_count"] else "🔢  Count      : (parse failed)")
    print(f"📡  Source     : {r.get('source', '?')}")
    print("━" * 52 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def scrape_multiple_stores(store_ids: list[str], results_file: str = "store_results.json") -> list[dict]:
    results = []
    print(f"\n🚀 Starting batch scrape for {len(store_ids)} stores\n")

    # ── Resume: load existing results if file already exists ─────────────
    if os.path.exists(results_file):
        try:
            with open(results_file, "r", encoding="utf-8") as f:
                results = json.load(f)
            print(f"📂 Resuming — loaded {len(results)} existing results from {results_file}")
        except Exception as e:
            print(f"⚠️  Could not load existing results: {e} — starting fresh")
            results = []

    already_done = {str(r["store_id"]) for r in results}

    for i, sid in enumerate(store_ids, 1):
        print(f"\n==============================")
        print(f"🔢 {i}/{len(store_ids)} → Store {sid}")
        print(f"==============================")

        if sid in already_done:
            print(f"⏭️  Already scraped — skipping")
            continue

        try:
            result = scrape_store_item_count(sid)
        except Exception as e:
            print(f"❌ Failed store {sid}: {e}")
            result = {
                "store_id":        sid,
                "url":             None,
                "item_count_text": None,
                "item_count":      None,
                "error":           str(e),
                "source":          "exception",
            }

        result["scraped_at"] = datetime.utcnow().isoformat()
        results.append(result)

        # ── Write immediately after every store ───────────────────────────
        try:
            with open(results_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"   💾 Saved → {results_file} ({len(results)} total)")
        except Exception as e:
            print(f"   ⚠️  Write failed: {e}")

    return results


def main():
    if len(sys.argv) > 1:
        # Single store ID passed on command line
        store_id = sys.argv[1]
        result = scrape_store_item_count(store_id)
        print(json.dumps(result, indent=2))
    else:
        # Batch mode: read from CSV
        csv_file = "stores_info_1_fixed.csv"
        print(f"📂 Loading store IDs from: {csv_file}")
        store_ids = load_store_ids_from_csv(csv_file)

        if not store_ids:
            print("❌ No store IDs found in CSV")
            return

        results = scrape_multiple_stores(store_ids)

        with open("store_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print("\n✅ All results saved to store_results.json")


if __name__ == "__main__":
    main()
