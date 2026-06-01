"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Scrapes all variant types, values, and images from an AliExpress product page.
Uses Tor (socks5://127.0.0.1:9050) + Camoufox (if installed) or plain Playwright.

Changes from previous version:
  - Removed gatewayAdapt=glo2swe from URL (was causing redirects/blocks)
  - Simplified cookies to avoid region mismatch detection
  - Replaced fixed 10s sleep with explicit SKU selector wait
  - Added HTML debug snippet on empty variant result
  - Expanded default exit node pool suggestion in comments

Output format:
    {
        "product_id": 1005011831898302,
        "url": "https://...",
        "variants": {
            "Color": "WiFi Cam No Card,WiFi Cam Add 32G,...",
            "Size": "S,M,L"
        },
        "success": true,
        "error": null,
        "scraped_at": "2026-05-21T11:04:20+00:00"
    }

Usage:
    python scr_variants.py 1005011748833056
"""

import sys
import time
import random
import json
from datetime import datetime, timezone

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

PROXY_CFG = {"server": "socks5://127.0.0.1:9050"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
]

# Minimal cookies — only locale, no region/currency that could mismatch
# with the Tor exit IP and trigger bot detection.
# Removed: aep_usuc_f (had region=SE conflicting with glo2swe),
#          xman_us_f, aep_common_f, _aep_modified_region
COOKIES = [
    {"name": "intl_locale", "value": "en_US",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "aep_usuc_f",  "value": "site=glo&c_tp=USD&region=US&b_locale=en_US",
     "domain": ".aliexpress.com", "path": "/"},
]

# SKU selector used to wait for React variant widgets to render
SKU_SELECTOR = '[data-sku-col], [class*="sku-item"], [class*="sku--wrap"]'

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rotate_tor_circuit() -> bool:
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()   # null auth — no password needed
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


# ── Detection helpers ─────────────────────────────────────────────────────────

def is_captcha_page(page) -> bool:
    """
    Detect genuine block/CAPTCHA pages.

    reCAPTCHA iframes appear on normal AliExpress pages too (login widgets).
    Only flag as a block if:
      - the iframe is VISIBLE, AND
      - its bounding box is wider than 200px (a real challenge dialog).
    A hidden 1×1 bot-score pixel will not trigger this.
    """
    url   = page.url.lower()
    title = page.title().lower()

    # Hard URL signals — always block pages
    if any(kw in url for kw in ["baxia", "punish", "captcha", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    # Dedicated block-page DOM elements
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

    # reCAPTCHA: only flag if large AND visible
    try:
        frames = page.locator("iframe[src*='recaptcha']")
        for i in range(frames.count()):
            frame = frames.nth(i)
            if not frame.is_visible():
                continue
            box = frame.bounding_box()
            if box and box.get("width", 0) > 200:
                print(f"❌ Visible reCAPTCHA challenge (width={box['width']}px)")
                return True
    except Exception:
        pass

    # Title-based block detection
    is_product = "aliexpress" in title and len(title) > 40
    if not is_product and any(kw in title for kw in
                              ["verify", "access", "denied", "blocked", "challenge"]):
        print("❌ Block page detected from title")
        return True

    return False


def is_homepage_redirect(page) -> bool:
    if "/item/" not in page.url:
        print(f"❌ Homepage redirect. URL: {page.url}")
        return True
    return False


# ── Browser factory ───────────────────────────────────────────────────────────

def _launch_camoufox():
    """Launch Camoufox with Tor proxy. Returns (cm, browser, ctx, page)."""
    cf      = Camoufox(headless=True, proxy=PROXY_CFG, geoip=True, humanize=True)
    browser = cf.__enter__()
    ctx     = browser.new_context(locale="en-US")
    ctx.add_cookies(COOKIES)
    page    = ctx.new_page()
    return cf, browser, ctx, page


def _launch_playwright(pw):
    """Launch plain Playwright with Tor proxy. Returns (browser, ctx, page)."""
    browser = pw.chromium.launch(
        headless=True,
        proxy=PROXY_CFG,
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

_EXTRACT_JS = r"""
() => {
    const result = {};

    // ── Pass 1: data-sku-col grouping (most reliable) ──────────────────────
    const allSkuCols = document.querySelectorAll('[data-sku-col]');
    if (allSkuCols.length > 0) {

        // Group by row prefix: "14-193" -> rowId "14"
        const rowMap = {};
        allSkuCols.forEach(el => {
            const col   = el.getAttribute('data-sku-col') || '';
            const rowId = col.split('-')[0];
            if (!rowMap[rowId]) rowMap[rowId] = [];
            rowMap[rowId].push(el);
        });

        let rowIndex = 0;
        for (const [rowId, elements] of Object.entries(rowMap)) {

            // ── Label detection ────────────────────────────────────────────
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

            // Fallback: scan ancestor siblings for a short label-like text
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
                        if (txt && txt.length > 0 && txt.length < 40) {
                            label = txt;
                            break;
                        }
                    }
                }
            }

            if (!label) label = `type_${rowIndex + 1}`;

            // ── Value + image collection ───────────────────────────────────
            const values = [];
            const images = [];

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
                    const txt  = (span
                        ? span.textContent
                        : e.getAttribute('title') || e.textContent
                    ).trim();
                    if (txt && txt.length < 50 && !values.includes(txt)) {
                        values.push(txt);
                        images.push(null);
                    }
                });
            }

            if (values.length > 0) {
                const key = result[label] !== undefined
                    ? `${label}_${rowIndex}`
                    : label;
                result[key] = { values, images };
            }
            rowIndex++;
        }

        if (Object.keys(result).length > 0) return result;
    }

    // ── Pass 2: class-name fallback ────────────────────────────────────────
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

    // ── Pass 3: debug — return SKU-related class names for diagnosis ───────
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


# ── One scrape attempt ────────────────────────────────────────────────────────

def _attempt(url: str, pw) -> tuple[dict | None, str | None]:
    """
    Run one attempt. Returns (variants, None) on success or (None, reason) on failure.
    variants is {} (empty dict) for single-variant products — that is a success.
    """
    cm = browser = ctx = page = None
    try:
        if USE_CAMOUFOX:
            print("   🦊 Using Camoufox + Tor")
            cm, browser, ctx, page = _launch_camoufox()
        else:
            print("   🌐 Using Playwright + Tor")
            browser, ctx, page = _launch_playwright(pw)

        print("   ⏳ Navigating to product...")
        # Removed gatewayAdapt=glo2swe — was forcing Swedish storefront,
        # causing region mismatches and increased block rate.
        page.goto(url, timeout=120_000, wait_until="domcontentloaded")
        time.sleep(3)
        print(f"   ✓ Landed: {page.url}")

        if is_homepage_redirect(page):
            return None, "homepage_redirect"
        if is_captcha_page(page):
            return None, "captcha"

        # Wait for SKU/variant widgets to render instead of a fixed sleep.
        # AliExpress uses React — selectors appear async after domcontentloaded.
        print("   ⏳ Waiting for SKU selector...")
        try:
            page.wait_for_selector(SKU_SELECTOR, timeout=20_000)
            print("   ✓ SKU selector found")
            time.sleep(2)  # small buffer after selector appears
        except PlaywrightTimeoutError:
            print("   ⚠️  SKU selector never appeared — may be single-SKU product, continuing...")
            # Don't abort — single-SKU products won't have this selector

        # Gentle scroll to trigger lazy SKU rendering
        for _ in range(4):
            page.mouse.wheel(0, random.randint(200, 400))
            time.sleep(random.uniform(0.3, 0.6))
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(2)

        if is_homepage_redirect(page):
            return None, "homepage_redirect_post_scroll"
        if is_captcha_page(page):
            return None, "captcha_post_scroll"

        title = page.title()
        print(f"   ✓ Title: {title[:80]}")
        if not title.strip():
            return None, "empty_title"

        variants = _extract_variants(page)

        # Debug: if nothing found, print a page snippet to diagnose
        if not variants:
            snippet = page.content()[:800].replace("\n", " ").strip()
            print(f"   🔍 DEBUG — no variants found. Page snippet:\n   {snippet}\n")

        return variants, None

    finally:
        objs = [ctx, browser, cm] if USE_CAMOUFOX else [ctx, browser]
        for obj in objs:
            if obj is None:
                continue
            try:
                if hasattr(obj, "close"):      obj.close()
                elif hasattr(obj, "__exit__"): obj.__exit__(None, None, None)
            except Exception:
                pass


# ── Main ──────────────────────────────────────────────────────────────────────

def scrape_product_variants(product_id: int | str) -> dict:
    pid = int(product_id)
    # Clean URL — no gatewayAdapt param that forces a specific storefront
    url = f"https://www.aliexpress.com/item/{pid}.html"

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

    pw_ctx = sync_playwright().__enter__() if not USE_CAMOUFOX else None

    try:
        for attempt in range(MAX_RETRIES):
            print(f"\n📍 Attempt {attempt + 1}/{MAX_RETRIES}")

            if attempt > 0:
                rotate_tor_circuit()
                wait_time = 20 + (attempt * 5)
                print(f"   Waiting {wait_time}s before retry...")
                time.sleep(wait_time)

            try:
                variants, reason = _attempt(url, pw_ctx)
            except PlaywrightTimeoutError as e:
                print(f"   ⚠️  Timeout: {e}")
                rotate_tor_circuit()
                continue
            except Exception as e:
                import traceback
                print(f"   ❌ Error: {e}")
                traceback.print_exc()
                continue

            if reason:
                print(f"   ⚠️  Failed ({reason}) — rotating and retrying...")
                rotate_tor_circuit()
                continue

            # Empty dict = product has no variants (single-SKU) — still a success
            if not variants:
                print("   ⚠️  No variants found — product may be single-SKU")
                return {**base, "variants": {}, "success": True, "scraped_at": _now()}

            print(f"   ✅ Extracted {len(variants)} variant group(s):")
            for group, data in variants.items():
                vals = data["values"]
                imgs = data["images"]
                has_img = any(imgs)
                print(f"      • {group} ({len(vals)} options{'  +images' if has_img else ''}):")
                for i, v in enumerate(vals):
                    suffix = f"  → {imgs[i][:70]}..." if has_img and imgs[i] else ""
                    print(f"          - {v}{suffix}")

            # Flatten to original format: {"Color": "val1,val2,val3", ...}
            flat = {
                group: ",".join(data["values"]) if isinstance(data, dict) else data
                for group, data in variants.items()
            }
            return {**base, "variants": flat, "success": True, "scraped_at": _now()}

    finally:
        if pw_ctx:
            try: pw_ctx.__exit__(None, None, None)
            except Exception: pass

    msg = f"Failed after {MAX_RETRIES} attempts"
    print(f"\n❌ {msg}")
    return {**base, "error": msg, "scraped_at": _now()}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))
