"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Scrapes all variant types and values from an AliExpress product page.
Uses Tor + Camoufox (if available) or plain Playwright.

Usage:
    python scr_variants.py 1005011748833056
"""

import sys
import os
import time
import random
import json
from datetime import datetime

try:
    from camoufox.sync_api import Camoufox
    USE_CAMOUFOX = True
except ImportError:
    USE_CAMOUFOX = False

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from stem import Signal
from stem.control import Controller

# ── Config ────────────────────────────────────────────────────────────────────

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
    {"name": "aep_usuc_f",           "value": "site=glo&c_tp=SEK&region=SE&b_locale=en_US",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "xman_us_f",            "value": "x_locale=en_US&x_site=SWE",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "aep_common_f",         "value": "F=F&reg=SE",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "_aep_modified_region", "value": "SE",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "intl_locale",          "value": "en_US",
     "domain": ".aliexpress.com", "path": "/"},
]

TOR_PROXY = {"server": "socks5://127.0.0.1:9050"}

# ── Tor ───────────────────────────────────────────────────────────────────────

def rotate_tor_circuit():
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("   Waiting 15s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {15 - i - 1}s remaining")
        print("✅ Tor circuit rotated")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


# ── Detection helpers ─────────────────────────────────────────────────────────

def is_captcha_page(page) -> bool:
    """
    Detect genuine block/CAPTCHA pages.

    Key fix: reCAPTCHA iframes appear on normal AliExpress pages too
    (e.g. embedded in login widgets). We only flag it as a block if the
    iframe is VISIBLE and takes up significant page area, or if other
    hard block signals are present.
    """
    page_url   = page.url.lower()
    page_title = page.title().lower()

    # Hard URL signals — these are always block pages
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    # Dedicated block-page selectors (not generic recaptcha)
    for selector in [
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha-box']",
        "iframe[src*='geetest']",
        "[class*='baxia']",
        "[class*='punish']",
    ]:
        try:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                print(f"❌ CAPTCHA widget detected: {selector}")
                return True
        except Exception:
            continue

    # reCAPTCHA: only flag if the iframe is large and visible
    # (a tiny hidden one is just a bot-score pixel, not a challenge)
    try:
        rc_frames = page.locator("iframe[src*='recaptcha']")
        if rc_frames.count() > 0:
            for i in range(rc_frames.count()):
                frame = rc_frames.nth(i)
                if not frame.is_visible():
                    continue
                box = frame.bounding_box()
                # A real CAPTCHA challenge iframe is at least 200px wide
                if box and box.get("width", 0) > 200:
                    print(f"❌ Visible reCAPTCHA challenge detected (width={box['width']}px)")
                    return True
    except Exception:
        pass

    # Title-based block detection
    is_product = "aliexpress" in page_title and len(page_title) > 40
    if not is_product and any(kw in page_title for kw in
                              ["verify", "access", "denied", "blocked", "challenge"]):
        print("❌ Block page detected from title")
        return True

    return False


def is_homepage_redirect(page, product_url: str) -> bool:
    """Returns True if we landed on the homepage instead of the product page."""
    if "/item/" not in page.url:
        print(f"❌ Homepage redirect detected. Current URL: {page.url}")
        return True
    return False


# ── Browser factory ───────────────────────────────────────────────────────────

def _launch_camoufox():
    """Launch Camoufox with Tor proxy. Returns (cm, browser, ctx, page)."""
    cf      = Camoufox(headless=True, proxy=TOR_PROXY, geoip=True, humanize=True)
    browser = cf.__enter__()
    ctx     = browser.new_context(locale="en-US")
    ctx.add_cookies(COOKIES)
    page = ctx.new_page()
    return cf, browser, ctx, page


def _launch_playwright(p):
    """Launch plain Playwright with Tor proxy. Returns (browser, ctx, page)."""
    browser = p.chromium.launch(
        headless=True,
        proxy=TOR_PROXY,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    ctx = browser.new_context(
        viewport=random.choice(VIEWPORTS),
        user_agent=random.choice(USER_AGENTS),
        timezone_id=random.choice([
            "America/New_York", "America/Chicago",
            "America/Denver",   "America/Los_Angeles",
        ]),
    )
    ctx.add_cookies(COOKIES)
    page = ctx.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    page.add_init_script(
        "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});"
    )
    return browser, ctx, page


# ── Variant extraction ────────────────────────────────────────────────────────

def _extract_variants(page) -> dict[str, str]:
    """
    Extract all SKU variant groups from the rendered page.

    Pass 1: data-sku-col/data-sku-row attributes (most reliable).
    Pass 2: class-name based scan (fallback for older format).
    Pass 3: debug dump of all SKU-related class names.
    """

    # ── Pass 1: data-sku-row grouping ─────────────────────────────────────
    variants = page.evaluate(r"""
    () => {
        const result = {};
        const allSkuCols = document.querySelectorAll('[data-sku-col]');
        if (allSkuCols.length === 0) return null;

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
            const firstEl = elements[0];
            const parent  = firstEl.closest(
                '[class*="sku-item--box"], [class*="skuItem"], [class*="sku-wrap"]'
            ) || firstEl.parentElement?.parentElement;

            if (parent) {
                const titleCandidates = parent.querySelectorAll(
                    '[class*="sku-item--title"], [class*="sku-title"], ' +
                    '[class*="property-title"], [class*="sku-item--property"], ' +
                    '[class*="skuTitle"], [class*="attr-title"], ' +
                    'span[class*="title"], div[class*="label"]'
                );
                if (titleCandidates.length > 0) {
                    label = titleCandidates[0].textContent.trim().replace(/:$/, '').trim();
                }
                if (!label) {
                    const gp = parent.parentElement;
                    if (gp) {
                        for (const child of gp.children) {
                            const txt = child.textContent.trim();
                            if (txt && txt.length < 50 && !child.querySelector('[data-sku-col]')) {
                                label = txt.replace(/:$/, '').trim();
                                break;
                            }
                        }
                    }
                }
            }
            if (!label) label = `type_${rowIndex + 1}`;

            const values = [];
            elements.forEach(el => {
                const img = el.querySelector('img');
                if (img) {
                    const alt = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
                    if (alt && !values.includes(alt)) values.push(alt);
                }
            });
            if (values.length === 0) {
                elements.forEach(el => {
                    const span = el.querySelector('span');
                    const txt  = (span ? span.textContent : el.textContent).trim();
                    if (txt && txt.length < 50 && !values.includes(txt)) values.push(txt);
                });
            }
            if (values.length > 0) {
                const key   = result[label] !== undefined ? `${label}_${rowIndex}` : label;
                result[key] = values.join(',');
            }
            rowIndex++;
        }
        return Object.keys(result).length > 0 ? result : null;
    }
    """)
    if variants:
        return variants

    # ── Pass 2: class-name based scan ─────────────────────────────────────
    variants = page.evaluate(r"""
    () => {
        const result = {};
        const skuContainers = [...document.querySelectorAll('*')].filter(el => {
            const cls = (el.className || '');
            if (typeof cls !== 'string') return false;
            return cls.includes('sku') && (
                cls.includes('row') || cls.includes('group') ||
                cls.includes('skus') || cls.includes('wrap')
            );
        });
        skuContainers.forEach((container, idx) => {
            if (container.querySelectorAll('img, span').length > 50) return;
            const values = [];
            container.querySelectorAll('img').forEach(img => {
                const alt = (img.getAttribute('alt') || '').trim();
                if (alt && alt.length < 40 && !values.includes(alt)) values.push(alt);
            });
            if (values.length === 0) {
                container.querySelectorAll('span').forEach(span => {
                    const txt = span.textContent.trim();
                    if (txt && txt.length < 30 && /^[a-zA-Z0-9\s\/\-\.]+$/.test(txt)
                        && !values.includes(txt)) values.push(txt);
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
                if (!result[label]) result[label] = values.join(',');
            }
        });
        return Object.keys(result).length > 0 ? result : null;
    }
    """)
    if variants:
        return variants

    # ── Pass 3: debug dump ────────────────────────────────────────────────
    all_sku_classes = page.evaluate(r"""
    () => {
        const found = new Set();
        document.querySelectorAll('*').forEach(el => {
            if (el.className && typeof el.className === 'string')
                el.className.split(' ').forEach(c => {
                    if (c.toLowerCase().includes('sku') ||
                        c.toLowerCase().includes('variant') ||
                        c.toLowerCase().includes('property') ||
                        c.toLowerCase().includes('option')) found.add(c);
                });
        });
        return [...found];
    }
    """)
    print(f"   ℹ️  SKU-related classes on page: {all_sku_classes[:30]}")
    return {}


# ── One attempt ───────────────────────────────────────────────────────────────

def _attempt(url: str, use_pw) -> tuple[dict | None, str | None]:
    """
    Run a single scrape attempt.
    Returns (variants_dict, None) on success or (None, reason) on failure.
    """
    cm = browser = ctx = page = None
    try:
        if USE_CAMOUFOX:
            print("   🦊 Using Camoufox")
            cm, browser, ctx, page = _launch_camoufox()
        else:
            print("   🌐 Using plain Playwright")
            browser, ctx, page = _launch_playwright(use_pw)

        print("   ⏳ Navigating directly to product...")
        page.goto(url, timeout=120_000, wait_until="domcontentloaded")
        time.sleep(3)
        print(f"   ✓ Landed URL: {page.url}")

        if is_homepage_redirect(page, url):
            return None, "homepage_redirect"

        if is_captcha_page(page):
            return None, "captcha"

        print("   ⏳ Waiting for page JS to render (10s)...")
        time.sleep(10)

        for _ in range(4):
            page.mouse.wheel(0, random.randint(200, 400))
            time.sleep(random.uniform(0.3, 0.6))
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(2)

        if is_homepage_redirect(page, url):
            return None, "homepage_redirect_post_scroll"

        if is_captcha_page(page):
            return None, "captcha_post_scroll"

        title = page.title()
        print(f"   ✓ Page title: {title[:80]}")

        if not title.strip():
            return None, "empty_title"

        variants = _extract_variants(page)
        return variants, None

    finally:
        for obj in ([ctx, browser, cm] if USE_CAMOUFOX else [ctx, browser]):
            if obj is None:
                continue
            try:
                if hasattr(obj, "close"):      obj.close()
                elif hasattr(obj, "__exit__"): obj.__exit__(None, None, None)
            except Exception:
                pass


# ── Main scraper function ─────────────────────────────────────────────────────

def scrape_product_variants(product_id: int | str) -> dict:
    pid = int(product_id)
    url = f"https://www.aliexpress.com/item/{pid}.html?gatewayAdapt=glo2swe"

    print(f"\n🔍 Variant Scraper  ({'Camoufox' if USE_CAMOUFOX else 'Playwright'})")
    print("━" * 50)
    print(f"📦 Product ID : {pid}")
    print(f"🔗 URL        : {url}")
    print("━" * 50)

    base_result = {
        "product_id": pid,
        "url":        url,
        "variants":   {},
        "success":    False,
        "error":      None,
        "scraped_at": datetime.utcnow().isoformat(),
    }

    # Plain Playwright needs a single sync_playwright() context across retries
    pw_ctx = sync_playwright().__enter__() if not USE_CAMOUFOX else None

    try:
        for attempt in range(MAX_RETRIES):
            print(f"\n📍 Attempt {attempt + 1}/{MAX_RETRIES}")

            if attempt > 0:
                print("🔄 Rotating Tor circuit...")
                rotate_tor_circuit()
                wait_time = 20 + (attempt * 5)
                print(f"   Waiting {wait_time}s before next attempt...")
                time.sleep(wait_time)

            try:
                variants, reason = _attempt(url, pw_ctx)
            except PlaywrightTimeoutError as e:
                print(f"   ⚠️ Timeout: {e}")
                rotate_tor_circuit()
                continue
            except Exception as e:
                import traceback
                print(f"   ❌ Error: {e}")
                traceback.print_exc()
                continue

            if reason:
                print(f"   ⚠️ Failed ({reason}) — rotating and retrying...")
                rotate_tor_circuit()
                continue

            # variants is None means extraction returned nothing
            if variants is None:
                print("   ⚠️ No variants found — product may have no options")
                return {**base_result, "variants": {}, "success": True,
                        "scraped_at": datetime.utcnow().isoformat()}

            print(f"   ✅ Extracted {len(variants)} variant type(s):")
            for vtype, values in variants.items():
                print(f"      • {vtype}: {values}")

            return {**base_result, "variants": variants, "success": True,
                    "scraped_at": datetime.utcnow().isoformat()}

    finally:
        if pw_ctx:
            try: pw_ctx.__exit__(None, None, None)
            except Exception: pass

    print(f"\n❌ Failed after {MAX_RETRIES} attempts")
    return {**base_result, "error": f"Failed after {MAX_RETRIES} attempts",
            "scraped_at": datetime.utcnow().isoformat()}


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))
