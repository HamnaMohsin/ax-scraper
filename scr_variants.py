"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Forces Swedish storefront via gatewayAdapt=glo2swe + SE cookies.
Uses Tor (socks5://127.0.0.1:9050) + plain Playwright.

Returns per variant group:
    {"Color": {"values": ["Red", "Blue"], "images": ["https://...", null]}}

Usage:
    python scr_variants.py 1005011748833056
"""

import sys
import time
import random
import json
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from stem import Signal
from stem.control import Controller

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
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "xman_us_f",            "value": "x_locale=en_US&x_site=SWE",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "aep_common_f",         "value": "F=F&reg=SE",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "_aep_modified_region", "value": "SE",
     "domain": ".aliexpress.com",    "path": "/"},
    {"name": "intl_locale",          "value": "en_US",
     "domain": ".aliexpress.com",    "path": "/"},
]

SKU_SELECTOR = '[data-sku-col], [class*="sku-item"], [class*="sku--wrap"]'


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


def is_captcha_page(page) -> bool:
    page_url   = page.url.lower()
    page_title = page.title().lower()

    # Hard URL signals
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    # Visible block-page DOM elements only
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

    # reCAPTCHA: ONLY flag if visible AND wider than 200px
    # Normal product pages have a hidden 1x1 bot-score iframe — never flag that
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
    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    if not is_product_page and any(kw in page_title for kw in
                                   ["verify", "access", "denied", "blocked", "challenge"]):
        print("❌ Block page detected from title")
        return True

    return False


_EXTRACT_JS = r"""
() => {
    const result = {};

    // ── Pass 1: data-sku-col grouping (most reliable) ──────────────────────
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
                const key = result[label] !== undefined ? `${label}_${rowIndex}` : label;
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

    // ── Pass 3: debug ──────────────────────────────────────────────────────
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


def scrape_product_variants(product_id: int | str) -> dict:
    pid = int(product_id)
    url = f"https://www.aliexpress.com/item/{pid}.html?gatewayAdapt=glo2swe"

    print(f"\n🔍 Variant Scraper  (Playwright / Tor)")
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

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            page = browser.new_page(
                viewport=random_viewport(),
                user_agent=random.choice(USER_AGENTS),
                timezone_id=random.choice([
                    "America/New_York", "America/Chicago",
                    "America/Denver",   "America/Los_Angeles",
                ]),
            )
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page.add_init_script(
                "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});"
            )
            page.context.add_cookies(COOKIES)

            try:
                print("📡 Loading page...")
                page.goto(url, timeout=120_000, wait_until="domcontentloaded")
                time.sleep(2)

                current_url = page.url
                if current_url != url:
                    print(f"⚠️  Redirected to: {current_url}")

                # Wait for React to fully render BEFORE checking for CAPTCHA
                # Checking immediately after domcontentloaded catches login widgets
                # that disappear once the real page content loads
                print("⏳ Waiting for page to render...")
                time.sleep(8)

                if is_captcha_page(page):
                    print("⚠️  CAPTCHA detected — rotating IP and retrying...")
                    browser.close()
                    continue

                print("⏳ Scrolling to load variants...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 400))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️  Scroll error: {e}")

                if is_captcha_page(page):
                    print("⚠️  CAPTCHA after scroll — rotating IP and retrying...")
                    browser.close()
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
                    browser.close()
                    continue

                variants = _extract_variants(page)
                browser.close()

                if not variants:
                    print("   ⚠️  No variants found — product may be single-SKU")
                    return {**base, "variants": {}, "success": True, "scraped_at": _now()}

                print(f"   ✅ Extracted {len(variants)} variant group(s):")
                for group, data in variants.items():
                    vals    = data["values"]
                    imgs    = data["images"]
                    has_img = any(imgs)
                    print(f"      • {group} ({len(vals)} options{'  +images' if has_img else ''}):")
                    for i, v in enumerate(vals):
                        suffix = f"  → {imgs[i][:70]}..." if has_img and imgs[i] else ""
                        print(f"          - {v}{suffix}")

                # Return values AND images per group — consumed by main.py
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
                try: browser.close()
                except Exception: pass
                continue

            except Exception as e:
                import traceback
                print(f"❌ Error on attempt {attempt + 1}: {e}")
                traceback.print_exc()
                try: browser.close()
                except Exception: pass
                continue

    msg = f"Failed after {MAX_RETRIES} attempts"
    print(f"\n❌ {msg}")
    return {**base, "error": msg, "scraped_at": _now()}


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))
