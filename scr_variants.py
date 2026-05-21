"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Proxy strategy (in order of preference):
  1. Residential proxy pool  — PROXY_URL env var (best)
  2. Direct connection       — PROXY_URL="direct" or unset
  3. Tor                     — PROXY_URL="tor" (last resort, often blocked)

Usage:
    python scr_variants.py 1005011748833056

    PROXY_URL=socks5://user:pass@host:port python scr_variants.py 1005011748833056
    PROXY_URL=direct python scr_variants.py 1005011748833056
    PROXY_URL=tor    python scr_variants.py 1005011748833056
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

# Tor is optional — only imported when PROXY_URL=tor
_tor_available = False
try:
    from stem import Signal
    from stem.control import Controller
    _tor_available = True
except ImportError:
    pass

# ── Config ────────────────────────────────────────────────────────────────────

MAX_RETRIES = 3

# Read proxy from environment
_proxy_env = os.environ.get("PROXY_URL", "").strip()
if _proxy_env.lower() == "tor":
    PROXY_MODE = "tor"
    PROXY_CFG  = {"server": "socks5://127.0.0.1:9050"}
elif _proxy_env and _proxy_env.lower() != "direct":
    PROXY_MODE = "custom"
    PROXY_CFG  = {"server": _proxy_env}
else:
    PROXY_MODE = "direct"
    PROXY_CFG  = None

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

# ── Tor helpers ───────────────────────────────────────────────────────────────
TOR_PROXY = {"server": "socks5://127.0.0.1:9050"}

def rotate_tor_circuit():
    if not _tor_available:
        print("   ⚠️ stem not installed — cannot rotate Tor circuit")
        return False
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


def _maybe_rotate():
    """Rotate circuit only when using Tor; no-op otherwise."""
    if PROXY_MODE == "tor":
        rotate_tor_circuit()


# ── Detection helpers ─────────────────────────────────────────────────────────

def is_captcha_page(page) -> bool:
    page_url   = page.url.lower()
    page_title = page.title().lower()

    # Hard URL signals
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "_____tmd_____"]):
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

    # reCAPTCHA: only a large visible iframe is a real challenge
    try:
        rc_frames = page.locator("iframe[src*='recaptcha']")
        for i in range(rc_frames.count()):
            frame = rc_frames.nth(i)
            if not frame.is_visible():
                continue
            box = frame.bounding_box()
            if box and box.get("width", 0) > 200:
                print(f"❌ Visible reCAPTCHA challenge (width={box['width']}px)")
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


def is_homepage_redirect(page) -> bool:
    if "/item/" not in page.url:
        print(f"❌ Homepage redirect. URL: {page.url}")
        return True
    return False


# ── Browser factory ───────────────────────────────────────────────────────────

def _launch_camoufox():
    kwargs = dict(headless=True, geoip=True, humanize=True)
    if PROXY_CFG:
        kwargs["proxy"] = PROXY_CFG
    cf      = Camoufox(**kwargs)
    browser = cf.__enter__()
    ctx     = browser.new_context(locale="en-US")
    ctx.add_cookies(COOKIES)
    page = ctx.new_page()
    return cf, browser, ctx, page


def _launch_playwright(p):
    launch_kwargs = dict(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    if PROXY_CFG:
        launch_kwargs["proxy"] = PROXY_CFG

    browser = p.chromium.launch(**launch_kwargs)
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

def _extract_variants(page) -> dict:
    """
    Extract all SKU variant groups from the rendered page.

    Returns a dict of variant groups, each with label, values and image URLs:
    {
        "Color": {
            "values": ["WiFi Cam No Card", "WiFi Cam Add 32G", ...],
            "images": ["https://...jpg", "https://...jpg", ...]   # parallel list, None for text variants
        },
        "Size": {
            "values": ["S", "M", "L"],
            "images": [None, None, None]
        }
    }

    Pass 1: data-sku-col grouping with deep label search (most reliable).
    Pass 2: class-name based scan (fallback).
    Pass 3: debug dump of SKU-related class names.
    """

    # ── Pass 1: data-sku-col grouping ─────────────────────────────────────
    variants = page.evaluate(r"""
    () => {
        const result = {};

        // Group all [data-sku-col] elements by their row prefix (e.g. "14" from "14-193")
        const allSkuCols = document.querySelectorAll('[data-sku-col]');
        if (allSkuCols.length === 0) return null;

        const rowMap = {};  // rowId -> [{el, skuCol}]
        allSkuCols.forEach(el => {
            const col   = el.getAttribute('data-sku-col') || '';
            const rowId = col.split('-')[0];
            if (!rowMap[rowId]) rowMap[rowId] = [];
            rowMap[rowId].push(el);
        });

        let rowIndex = 0;
        for (const [rowId, elements] of Object.entries(rowMap)) {

            // ── Label detection ──────────────────────────────────────────
            // Structure: sku-item--property > [sku-item--title, extend--wrap > ... > sku-item--box > sku-item--skus]
            // Walk up until we hit the sku-item--property container, then
            // read its sku-item--title child. The title span contains e.g.
            // "Color: <span>WiFi Cam Add 32G</span>" — we want only "Color".
            let label = null;
            let el = elements[0];

            for (let depth = 0; depth < 10 && el && !label; depth++) {
                el = el.parentElement;
                if (!el) break;
                const cls = el.className || '';
                if (typeof cls === 'string' && cls.includes('sku-item--property')) {
                    // Found the property wrapper — grab the title sibling
                    const titleEl = el.querySelector('[class*="sku-item--title"]');
                    if (titleEl) {
                        // First text node before any colon or nested span
                        const firstSpan = titleEl.querySelector('span');
                        const raw = firstSpan
                            ? (firstSpan.childNodes[0] && firstSpan.childNodes[0].nodeType === 3
                                ? firstSpan.childNodes[0].textContent
                                : firstSpan.textContent)
                            : titleEl.textContent;
                        label = raw.split(':')[0].replace(/\u00a0/g, '').trim();
                    }
                    break;
                }
            }

            // Fallback: scan ancestor siblings for any short label-like text
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

            // ── Value + image collection ─────────────────────────────────
            const values = [];
            const images = [];

            // Image-based variants (colour swatches etc.)
            const hasImages = elements.some(e => e.querySelector('img'));
            if (hasImages) {
                elements.forEach(el => {
                    const img = el.querySelector('img');
                    if (img) {
                        const alt = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
                        if (alt && !values.includes(alt)) {
                            values.push(alt);
                            images.push(img.getAttribute('src') || null);
                        }
                    }
                });
            }

            // Text-based variants (size buttons etc.)
            if (values.length === 0) {
                elements.forEach(el => {
                    const span = el.querySelector('span');
                    const txt  = (span ? span.textContent
                                       : el.getAttribute('title') || el.textContent
                                 ).trim();
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
            const images = [];
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
                    if (txt && txt.length < 30 && /^[a-zA-Z0-9\s\/\-\.]+$/.test(txt)
                        && !values.includes(txt)) {
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

def _attempt(url: str, pw) -> tuple[dict | None, str | None]:
    cm = browser = ctx = page = None
    try:
        if USE_CAMOUFOX:
            print("   🦊 Using Camoufox")
            cm, browser, ctx, page = _launch_camoufox()
        else:
            print("   🌐 Using plain Playwright")
            browser, ctx, page = _launch_playwright(pw)

        print("   ⏳ Navigating directly to product...")
        page.goto(url, timeout=120_000, wait_until="domcontentloaded")
        time.sleep(3)
        print(f"   ✓ Landed URL: {page.url}")

        if is_homepage_redirect(page):
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

        if is_homepage_redirect(page):
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
        objs = [ctx, browser, cm] if USE_CAMOUFOX else [ctx, browser]
        for obj in objs:
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

    print(f"\n🔍 Variant Scraper  ({'Camoufox' if USE_CAMOUFOX else 'Playwright'} / {PROXY_MODE})")
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

    pw_ctx = sync_playwright().__enter__() if not USE_CAMOUFOX else None

    try:
        for attempt in range(MAX_RETRIES):
            print(f"\n📍 Attempt {attempt + 1}/{MAX_RETRIES}")

            if attempt > 0:
                _maybe_rotate()
                wait_time = 20 + (attempt * 5)
                print(f"   Waiting {wait_time}s before next attempt...")
                time.sleep(wait_time)

            try:
                variants, reason = _attempt(url, pw_ctx)
            except PlaywrightTimeoutError as e:
                print(f"   ⚠️ Timeout: {e}")
                _maybe_rotate()
                continue
            except Exception as e:
                import traceback
                print(f"   ❌ Error: {e}")
                traceback.print_exc()
                continue

            if reason:
                print(f"   ⚠️ Failed ({reason}) — rotating and retrying...")
                _maybe_rotate()
                continue

            if not variants:
                print("   ⚠️ No variants found — product may have no options")
                return {**base_result, "variants": {}, "success": True,
                        "scraped_at": datetime.utcnow().isoformat()}

            print(f"   ✅ Extracted {len(variants)} variant type(s):")
            for vtype, data in variants.items():
                vals = data.get("values", data) if isinstance(data, dict) else data
                imgs = data.get("images", []) if isinstance(data, dict) else []
                has_imgs = any(imgs)
                print(f"      • {vtype} ({len(vals)} options{'  +images' if has_imgs else ''}):")
                for i, v in enumerate(vals):
                    img_hint = f"  → {imgs[i][:60]}..." if has_imgs and imgs[i] else ""
                    print(f"          - {v}{img_hint}")

            flat_variants = {
                vtype: data["values"] if isinstance(data, dict) else data
                for vtype, data in variants.items()
            }
            return {**base_result, "variants": flat_variants, "success": True,
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
