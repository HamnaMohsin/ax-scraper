"""
scr_variants.py — AliExpress Product Variant Scraper
Uses Camoufox (anti-detection Firefox) if available, falls back to Playwright.
Forces Swedish storefront via 3-layer approach:
  1. Pre-set SE cookies before navigation
  2. Route interceptor rewrites any regional redirect back to www + glo2swe
  3. Stockholm timezone + geolocation on browser context
"""

import sys
import re
import time
import random
import json
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from stem import Signal
from stem.control import Controller

try:
    from camoufox.sync_api import Camoufox
    USE_CAMOUFOX = True
    print("✅ Using camoufox (anti-detection Firefox)")
except ImportError:
    USE_CAMOUFOX = False
    print("⚠️  camoufox not found, falling back to plain Playwright")

MAX_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
]

COOKIES = [
    {"name": "aep_usuc_f",           "value": "site=swe&c_tp=SEK&region=SE&b_locale=en_US",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "xman_us_f",            "value": "x_locale=en_US&x_site=SWE",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "aep_common_f",         "value": "F=F&reg=SE",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "_aep_modified_region", "value": "SE",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "intl_locale",          "value": "en_US",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "acs_usuc_f",           "value": "x_locale=en_US&site=swe",
     "domain": ".aliexpress.com",    "path": "/"},
]

SKU_SELECTOR = '[data-sku-col], [class*="sku-item"], [class*="sku--wrap"]'
PROXY = {"server": "socks5://127.0.0.1:9050"}

# Regional domain pattern — anything that isn't www.aliexpress.com
_REGIONAL_RE = re.compile(r'https?://(www\.aliexpress\.us|[a-z]{2}\.aliexpress\.com)/')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


def rotate_tor_circuit() -> bool:
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
            print("   Waiting 15s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {15 - i - 1}s remaining")
        print("✅ Tor circuit rotated")
        return True
    except Exception as e:
        print(f"⚠️  Could not rotate Tor circuit: {e}")
        return False


# ─────────────────────────────────────────────
# SWEDEN FORCING
# ─────────────────────────────────────────────

def build_sweden_url(url: str) -> str:
    """Normalize any regional domain to www.aliexpress.com + force glo2swe."""
    url = re.sub(r'https?://[a-z]{2}\.aliexpress\.com', 'https://www.aliexpress.com', url)
    url = re.sub(r'https?://www\.aliexpress\.us',        'https://www.aliexpress.com', url)
    if 'gatewayAdapt=' in url:
        url = re.sub(r'gatewayAdapt=[^&]+', 'gatewayAdapt=glo2swe', url)
    else:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}gatewayAdapt=glo2swe"
    return url


def install_sweden_headers(page) -> None:
    """Set Accept-Language to hint Swedish/EU locale."""
    page.set_extra_http_headers({
        "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
    })


def install_geo_redirect_interceptor(page) -> None:
    """
    Intercept any mid-navigation redirect to a regional AliExpress domain
    and rewrite it back to www.aliexpress.com?gatewayAdapt=glo2swe.
    """
    def handle_route(route, request):
        url = request.url
        if _REGIONAL_RE.match(url) and "/item/" in url:
            fixed = re.sub(
                r'https?://(www\.aliexpress\.us|[a-z]{2}\.aliexpress\.com)',
                'https://www.aliexpress.com',
                url,
            )
            fixed = re.sub(r'gatewayAdapt=[^&]+', 'gatewayAdapt=glo2swe', fixed)
            if 'gatewayAdapt' not in fixed:
                sep = "&" if "?" in fixed else "?"
                fixed = f"{fixed}{sep}gatewayAdapt=glo2swe"
            print(f"🔀 Geo-redirect intercepted → rewriting to global/SWE")
            print(f"   FROM: {url[:80]}")
            print(f"   TO  : {fixed[:80]}")
            route.continue_(url=fixed)
        else:
            route.continue_()

    page.route("**aliexpress**", handle_route)
    print("🛡️  Geo-redirect interceptor active")


# ─────────────────────────────────────────────
# PAGE STATE DETECTION
# ─────────────────────────────────────────────

def detect_page_state(page) -> str:
    """
    Returns 'product' | 'captcha' | 'blocked' | 'unknown'.
    Uses AliExpress-specific DOM signals rather than generic h1/title checks.
    """
    url   = page.url.lower()
    title = ""
    try:
        title = page.title().lower()
    except Exception:
        pass

    # Hard URL signals
    if any(kw in url for kw in ["baxia", "punish", "captcha", "_____tmd_____"]):
        print("🚫 Page state: CAPTCHA (url keyword)")
        return "captcha"

    # reCAPTCHA v2 interactive challenge — bframe only (not anchor/v3)
    try:
        bframe = page.locator("iframe[src*='recaptcha/api2/bframe']")
        if bframe.count() > 0:
            print("🚫 Page state: CAPTCHA (reCAPTCHA v2 bframe)")
            return "captcha"
    except Exception:
        pass

    # Full-width reCAPTCHA iframe = real challenge page
    try:
        frames = page.locator("iframe[src*='recaptcha']")
        for i in range(frames.count()):
            frame = frames.nth(i)
            if not frame.is_visible():
                continue
            box = frame.bounding_box()
            if box and box.get("width", 0) > 200:
                # Only a real block if no product DOM exists alongside it
                product_present = False
                for ps in ['[data-pl="product-title"]', '[class*="product-title--wrap"]',
                           '[class*="price--current"]']:
                    try:
                        if page.locator(ps).count() > 0:
                            product_present = True
                            break
                    except Exception:
                        pass
                if not product_present:
                    print(f"🚫 Page state: CAPTCHA (reCAPTCHA w={box['width']}px, no product DOM)")
                    return "captcha"
                else:
                    print(f"ℹ️  reCAPTCHA present (w={box['width']}px) but product DOM exists — v3 scoring")
    except Exception:
        pass

    # Product-specific signals (require ≥2 to confirm)
    product_signals = [
        '[data-pl="product-title"]',
        '[class*="product-title--wrap"]',
        '[class*="buy-now"]',
        '[class*="add-to-cart"]',
        '#nav-specification',
        '[class*="product-main"]',
        '[class*="price--current"]',
        SKU_SELECTOR,
    ]
    found = sum(
        1 for s in product_signals
        if _safe_count(page, s) > 0
    )
    if found >= 2:
        print(f"✅ Page state: PRODUCT ({found} product signals)")
        return "product"

    # Captcha DOM nodes
    captcha_nodes = [
        ".baxia-punish", "#captcha-verify", "[id*='captcha-box']",
        "iframe[src*='geetest']", "[class*='baxia']", "[class*='punish']",
    ]
    if any(_safe_count(page, s) > 0 for s in captcha_nodes):
        print("🚫 Page state: CAPTCHA (captcha DOM node)")
        return "captcha"

    block_kw = ["verify", "access denied", "blocked", "challenge", "security check"]
    if any(kw in title for kw in block_kw):
        print(f"🚫 Page state: BLOCKED (title: '{title[:60]}')")
        return "blocked"

    print(f"⚠️  Page state: UNKNOWN (signals={found}, title='{title[:60]}')")
    return "unknown"


def _safe_count(page, selector: str) -> int:
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def wait_for_product_dom(page, timeout_ms: int = 20000) -> bool:
    """Wait for at least one product-specific selector. Returns True if found."""
    print(f"⏳ Waiting up to {timeout_ms // 1000}s for product DOM...")
    for sel in ['[data-pl="product-title"]', '[class*="product-title--wrap"]',
                '[class*="price--current"]', SKU_SELECTOR]:
        try:
            page.wait_for_selector(sel, timeout=timeout_ms)
            print(f"✅ Product DOM confirmed via: {sel}")
            return True
        except Exception:
            pass
    print("⚠️  Product DOM not found within timeout")
    return False


# ─────────────────────────────────────────────
# BROWSER LAUNCH
# ─────────────────────────────────────────────

def _launch_browser():
    """
    Launch browser and return (page, cleanup_fn).
    Applies full Sweden-forcing on both Camoufox and Playwright paths.
    """
    if USE_CAMOUFOX:
        cm      = Camoufox(headless=True, proxy=PROXY, geoip=True, humanize=True)
        browser = cm.__enter__()

        # Camoufox: create context with Stockholm identity
        context = browser.new_context(
            timezone_id="Europe/Stockholm",
            locale="en-US",
            geolocation={"latitude": 59.3293, "longitude": 18.0686},
            permissions=["geolocation"],
        )
        context.add_cookies(COOKIES)
        page = context.new_page()

        def cleanup():
            try: cm.__exit__(None, None, None)
            except Exception: pass

    else:
        pw      = sync_playwright().__enter__()
        browser = pw.chromium.launch(
            headless=True,
            proxy=PROXY,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        # Stockholm identity — NOT random US timezones
        context = browser.new_context(
            viewport=random_viewport(),
            user_agent=random.choice(USER_AGENTS),
            timezone_id="Europe/Stockholm",
            locale="en-US",
            geolocation={"latitude": 59.3293, "longitude": 18.0686},
            permissions=["geolocation"],
        )
        context.add_cookies(COOKIES)
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page.add_init_script(
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});"
        )

        def cleanup():
            try: browser.close()
            except Exception: pass
            try: pw.__exit__(None, None, None)
            except Exception: pass

    # Apply Sweden forcing to the page (works for both paths)
    install_geo_redirect_interceptor(page)   # Fix 3: intercept redirects
    install_sweden_headers(page)             # Fix 1: Accept-Language header
    print("🇸🇪 Sweden context applied (cookies + headers + Stockholm identity)")

    return page, cleanup


# ─────────────────────────────────────────────
# JS EXTRACTION (unchanged)
# ─────────────────────────────────────────────

_EXTRACT_JS = r"""
() => {
    const result = {};

    const allSkuCols = document.querySelectorAll('[data-sku-col]');
    if (allSkuCols.length > 0) {
        const rowMap = {};
        allSkuCols.forEach(el => {
            const col   = el.getAttribute('data-sku-col') || '';
            const rowId = col.split('-')[0];
            if (!rowMap[rowId]) rowMap[rowId] = [];
            rowMap[rowId].push(el);
        });

        let rowIndex = 0;
        for (const [rowId, elements] of Object.entries(rowMap)) {
            let label = null;
            let el    = elements[0];

            for (let depth = 0; depth < 10 && el && !label; depth++) {
                el = el.parentElement;
                if (!el) break;
                if (typeof el.className === 'string' &&
                    el.className.includes('sku-item--property')) {
                    const titleEl = el.querySelector('[class*="sku-item--title"]');
                    if (titleEl) {
                        const firstSpan = titleEl.querySelector('span');
                        const raw = firstSpan
                            ? (firstSpan.childNodes[0]?.nodeType === 3
                                ? firstSpan.childNodes[0].textContent
                                : firstSpan.textContent)
                            : titleEl.textContent;
                        label = raw.split(':')[0].replace(/\u00a0/g, '').trim();
                    }
                    break;
                }
            }

            if (!label) {
                el = elements[0];
                for (let depth = 0; depth < 8 && el && !label; depth++) {
                    el = el.parentElement;
                    if (!el) break;
                    const parent = el.parentElement;
                    if (!parent) continue;
                    for (const sibling of parent.children) {
                        if (sibling === el || sibling.contains(el)) continue;
                        if (sibling.querySelectorAll('[data-sku-col]').length > 0) continue;
                        const txt = sibling.textContent.trim().split(':')[0].trim();
                        if (txt && txt.length > 0 && txt.length < 40) { label = txt; break; }
                    }
                }
            }

            if (!label) label = `type_${rowIndex + 1}`;

            const values = [], images = [];
            const hasImages = elements.some(e => e.querySelector('img'));

            if (hasImages) {
                elements.forEach(e => {
                    const img = e.querySelector('img');
                    if (!img) return;
                    const alt = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
                    if (alt && !values.includes(alt)) {
                        values.push(alt);
                        images.push(img.getAttribute('src') || null);
                    }
                });
            }

            if (values.length === 0) {
                elements.forEach(e => {
                    const span = e.querySelector('span');
                    const txt  = (span ? span.textContent
                                       : e.getAttribute('title') || e.textContent).trim();
                    if (txt && txt.length < 50 && !values.includes(txt)) {
                        values.push(txt);
                        images.push(null);
                    }
                });
            }

            if (values.length > 0) {
                const key = result[label] !== undefined ? `${label}_${rowIndex}` : label;
                result[key] = { values, images };
            }
            rowIndex++;
        }
        if (Object.keys(result).length > 0) return result;
    }

    const skuContainers = [...document.querySelectorAll('*')].filter(el => {
        const cls = el.className || '';
        if (typeof cls !== 'string') return false;
        return cls.includes('sku') && (
            cls.includes('row') || cls.includes('group') ||
            cls.includes('skus') || cls.includes('wrap')
        );
    });

    skuContainers.forEach((container, idx) => {
        if (container.querySelectorAll('img, span').length > 50) return;
        const values = [], images = [];

        container.querySelectorAll('img').forEach(img => {
            const alt = (img.getAttribute('alt') || '').trim();
            if (alt && alt.length < 40 && !values.includes(alt)) {
                values.push(alt);
                images.push(img.getAttribute('src') || null);
            }
        });

        if (values.length === 0) {
            container.querySelectorAll('span').forEach(span => {
                const txt = span.textContent.trim();
                if (txt && txt.length < 30 &&
                    /^[a-zA-Z0-9\s\/\-\.]+$/.test(txt) &&
                    !values.includes(txt)) {
                    values.push(txt);
                    images.push(null);
                }
            });
        }

        if (values.length >= 2) {
            let label = null;
            const prev = container.previousElementSibling;
            if (prev) {
                const txt = prev.textContent.trim();
                if (txt && txt.length < 50) label = txt.replace(/:$/, '').trim();
            }
            if (!label) label = `variant_${idx + 1}`;
            if (!result[label]) result[label] = { values, images };
        }
    });

    if (Object.keys(result).length > 0) return result;

    const found = new Set();
    document.querySelectorAll('*').forEach(el => {
        if (el.className && typeof el.className === 'string')
            el.className.split(' ').forEach(c => {
                if (['sku','variant','property','option'].some(k =>
                    c.toLowerCase().includes(k))) found.add(c);
            });
    });
    return { __debug_classes__: [...found].slice(0, 30) };
}
"""


def _extract_variants(page) -> dict:
    result = page.evaluate(_EXTRACT_JS)
    if not result:
        return {}
    if "__debug_classes__" in result:
        print(f"   ℹ️  SKU classes on page: {result['__debug_classes__']}")
        return {}
    return result


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def scrape_product_variants(product_id: int | str) -> dict:
    pid = int(product_id)
    url = build_sweden_url(f"https://www.aliexpress.com/item/{pid}.html")

    print(f"\n🔍 Variant Scraper  ({'Camoufox' if USE_CAMOUFOX else 'Playwright'} / Tor)")
    print("━" * 50)
    print(f"📦 Product ID : {pid}")
    print(f"🔗 URL        : {url}")
    print("━" * 50)

    base = {
        "product_id": pid,
        "url":        url,
        "variants":   {},
        "success":    False,
        "error":      None,
        "scraped_at": _now(),
    }

    for attempt in range(MAX_RETRIES):
        print(f"\n📍 Attempt {attempt + 1}/{MAX_RETRIES}")

        if attempt > 0:
            print("🔄 Rotating Tor circuit...")
            rotate_tor_circuit()
            wait_time = 20 + (attempt * 5)
            print(f"   Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)

        page, cleanup = _launch_browser()

        try:
            print("📡 Loading page...")
            page.goto(url, timeout=120_000, wait_until="domcontentloaded")

            # Wait for JS to settle before any checks
            page.wait_for_timeout(4000)

            print(f"   Current URL: {page.url[:100]}")

            # Wait for product DOM
            product_loaded = wait_for_product_dom(page, timeout_ms=20000)

            # Definitive state check
            state = detect_page_state(page)

            if state in ("captcha", "blocked"):
                print(f"🚫 {state.upper()} confirmed — rotating IP and retrying...")
                cleanup()
                continue

            if state == "unknown" and not product_loaded:
                print("⚠️  Unknown state + no product DOM — rotating IP and retrying...")
                cleanup()
                continue

            print("✅ Confirmed product page — proceeding")

            # Scroll to trigger lazy-loaded variant widgets
            print("⏳ Scrolling to load variants...")
            try:
                for _ in range(3):
                    page.mouse.wheel(0, random.randint(150, 400))
                    time.sleep(random.uniform(0.2, 0.6))
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
            except Exception as e:
                print(f"⚠️  Scroll error: {e}")

            # Second state check after scroll
            state = detect_page_state(page)
            if state in ("captcha", "blocked") and not product_loaded:
                print(f"🚫 {state.upper()} after scroll — rotating IP and retrying...")
                cleanup()
                continue

            try:
                page.wait_for_selector(SKU_SELECTOR, timeout=10_000)
                print("   ✓ SKU selector found")
                time.sleep(1)
            except PlaywrightTimeoutError:
                print("   ⚠️  SKU selector not found — may be single-SKU, continuing...")

            title = page.title()
            print(f"   ✓ Title: {title[:80]}")
            if not title.strip():
                print("⚠️  Empty title — page did not load correctly")
                cleanup()
                continue

            variants = _extract_variants(page)
            cleanup()

            if not variants:
                print("   ⚠️  No variants found — product may be single-SKU")
                return {**base, "variants": {}, "success": True, "scraped_at": _now()}

            print(f"   ✅ Extracted {len(variants)} variant group(s):")
            for group, data in variants.items():
                vals, imgs = data["values"], data["images"]
                has_img = any(imgs)
                print(f"      • {group} ({len(vals)} options{'  +images' if has_img else ''}):")
                for i, v in enumerate(vals):
                    suffix = f"  → {imgs[i][:70]}..." if has_img and imgs[i] else ""
                    print(f"          - {v}{suffix}")

            flat = {
                group: {
                    "values": data["values"] if isinstance(data, dict) else [str(data)],
                    "images": data["images"] if isinstance(data, dict) else [],
                }
                for group, data in variants.items()
            }
            return {**base, "variants": flat, "success": True, "scraped_at": _now()}

        except PlaywrightTimeoutError as e:
            print(f"⚠️  Timeout on attempt {attempt + 1}: {e}")
            cleanup()
            continue

        except Exception as e:
            import traceback
            print(f"❌ Error on attempt {attempt + 1}: {e}")
            traceback.print_exc()
            cleanup()
            continue

    msg = f"Failed after {MAX_RETRIES} attempts"
    print(f"\n❌ {msg}")
    return {**base, "error": msg, "scraped_at": _now()}


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))
