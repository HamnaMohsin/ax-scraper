"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Scrapes all variant types and values from an AliExpress product page.
Uses the same Tor + plain playwright pattern as scraper3.py.

Variant types (Color, Size, Material, etc.) are detected from the page.
Each variant type's values are returned as a comma-separated string.

Example output:
    {
        "product_id": 1005011748833056,
        "url": "https://www.aliexpress.com/item/1005011748833056.html",
        "variants": {
            "Color": "black,white,pink,blue,Beige",
            "Size": "S,M,L,XL,XXL,XXXL,4XL"
        },
        "success": True,
        "error": None,
        "scraped_at": "2026-04-30T10:00:00"
    }

Usage:
    python scr_variants.py 1005011748833056
"""

import sys
import os
import re
import time
import random
import json
from datetime import datetime

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


# ── Captcha detection (same as scraper3) ─────────────────────────────────────

def is_captcha_page(page) -> bool:
    page_url   = page.url.lower()
    page_title = page.title().lower()

    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    for selector in ["iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
                     "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']"]:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA detected: {selector}")
                return True
        except Exception:
            continue

    is_product = "aliexpress" in page_title and len(page_title) > 40
    if not is_product and any(kw in page_title for kw in ["verify", "access", "denied", "blocked", "challenge"]):
        print("❌ Block page detected from title")
        return True

    return False


# ── Variant extraction ────────────────────────────────────────────────────────

def _extract_variants(page) -> dict[str, str]:
    """
    Extract all SKU variant groups from the rendered page.

    Strategy — multiple passes to handle AliExpress class name changes:
      Pass 1: Look for elements with 'sku' in their class, group by data-sku-row.
      Pass 2: Look for the SKU wrap container and parse its structure.
      Pass 3: Full DOM scan for any image/text option groups near a label.
    """

    # ── Pass 1: data-sku-row grouping (most reliable) ─────────────────────
    variants = page.evaluate(r"""
    () => {
        const result = {};

        // Group all sku option elements by their data-sku-row attribute
        const allSkuCols = document.querySelectorAll('[data-sku-col]');
        if (allSkuCols.length === 0) return null;

        // Build map: rowId -> [elements]
        const rowMap = {};
        allSkuCols.forEach(el => {
            const col = el.getAttribute('data-sku-col') || '';
            const rowId = col.split('-')[0];
            if (!rowMap[rowId]) rowMap[rowId] = [];
            rowMap[rowId].push(el);
        });

        let rowIndex = 0;
        for (const [rowId, elements] of Object.entries(rowMap)) {
            // ── Find label for this row ──────────────────────────────────
            // Walk up from first element to find a label/title sibling
            let label = null;
            const firstEl = elements[0];
            const parent  = firstEl.closest('[class*="sku-item--box"], [class*="skuItem"], [class*="sku-wrap"]')
                         || firstEl.parentElement?.parentElement;

            if (parent) {
                // Search for a title element near this group
                const titleCandidates = parent.querySelectorAll(
                    '[class*="sku-item--title"], [class*="sku-title"], ' +
                    '[class*="property-title"], [class*="sku-item--property"], ' +
                    '[class*="skuTitle"], [class*="attr-title"], ' +
                    'span[class*="title"], div[class*="label"]'
                );
                if (titleCandidates.length > 0) {
                    label = titleCandidates[0].textContent.trim().replace(/:$/, '').trim();
                }

                // Fallback: short sibling text before the options
                if (!label) {
                    const gp = parent.parentElement;
                    if (gp) {
                        for (const child of gp.children) {
                            const txt = child.textContent.trim();
                            if (txt && txt.length < 50 &&
                                !child.querySelector('[data-sku-col]')) {
                                label = txt.replace(/:$/, '').trim();
                                break;
                            }
                        }
                    }
                }
            }

            if (!label) label = `type_${rowIndex + 1}`;

            // ── Collect values ───────────────────────────────────────────
            const values = [];

            // Image options: use alt text
            elements.forEach(el => {
                const img = el.querySelector('img');
                if (img) {
                    const alt = (img.getAttribute('alt') || img.getAttribute('title') || '').trim();
                    if (alt && !values.includes(alt)) values.push(alt);
                }
            });

            // Text options: use visible text
            if (values.length === 0) {
                elements.forEach(el => {
                    const span = el.querySelector('span');
                    const txt  = (span ? span.textContent : el.textContent).trim();
                    if (txt && txt.length < 50 && !values.includes(txt)) values.push(txt);
                });
            }

            if (values.length > 0) {
                const key = result[label] !== undefined ? `${label}_${rowIndex}` : label;
                result[key] = values.join(',');
            }
            rowIndex++;
        }

        return Object.keys(result).length > 0 ? result : null;
    }
    """)

    if variants:
        return variants

    # ── Pass 2: class-name based scan (fallback for older page format) ────
    variants = page.evaluate(r"""
    () => {
        const result = {};

        // Find all elements whose class contains 'sku' and 'row' or 'group'
        const skuContainers = [...document.querySelectorAll('*')].filter(el => {
            const cls = (el.className || '');
            if (typeof cls !== 'string') return false;
            return (cls.includes('sku') && (cls.includes('row') || cls.includes('group') ||
                    cls.includes('skus') || cls.includes('wrap')));
        });

        skuContainers.forEach((container, idx) => {
            // Skip very large containers (they're wrappers)
            if (container.querySelectorAll('img, span').length > 50) return;

            const values = [];

            // Try images first
            container.querySelectorAll('img').forEach(img => {
                const alt = (img.getAttribute('alt') || '').trim();
                if (alt && alt.length < 40 && !values.includes(alt)) values.push(alt);
            });

            // Try text spans
            if (values.length === 0) {
                container.querySelectorAll('span').forEach(span => {
                    const txt = span.textContent.trim();
                    if (txt && txt.length < 30 && /^[a-zA-Z0-9\s\/\-\.]+$/.test(txt)
                        && !values.includes(txt)) {
                        values.push(txt);
                    }
                });
            }

            if (values.length >= 2) {
                // Try to find a label above
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

    # ── Pass 3: dump all classes so we can debug ──────────────────────────
    all_sku_classes = page.evaluate(r"""
    () => {
        const found = new Set();
        document.querySelectorAll('*').forEach(el => {
            if (el.className && typeof el.className === 'string') {
                el.className.split(' ').forEach(c => {
                    if (c.toLowerCase().includes('sku') ||
                        c.toLowerCase().includes('variant') ||
                        c.toLowerCase().includes('property') ||
                        c.toLowerCase().includes('option')) {
                        found.add(c);
                    }
                });
            }
        });
        return [...found];
    }
    """)
    print(f"   ℹ️  SKU-related classes on page: {all_sku_classes[:30]}")

    return {}


# ── Main scraper function ─────────────────────────────────────────────────────

def scrape_product_variants(product_id: int | str) -> dict:
    """
    Scrape all variant types and values for a given AliExpress product ID.
    Uses Tor + plain playwright, same as scraper3.py.
    """
    pid = int(product_id)
    url = f"https://www.aliexpress.com/item/{pid}.html"

    print(f"\n🔍 Variant Scraper")
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
                viewport=random.choice(VIEWPORTS),
                user_agent=random.choice(USER_AGENTS),
                timezone_id=random.choice([
                    "America/New_York", "America/Chicago",
                    "America/Denver",   "America/Los_Angeles",
                ]),
            )

            # Anti-detection scripts
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page.add_init_script(
                "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});"
            )

            # AliExpress region cookies
            page.context.add_cookies([
                {"name": "aep_usuc_f", "value": "site=glo&c_tp=SEK&region=SE&b_locale=en_US",
                 "domain": ".aliexpress.com", "path": "/"},
                {"name": "xman_us_f",  "value": "x_locale=en_US&x_site=SWE",
                 "domain": ".aliexpress.com", "path": "/"},
                {"name": "aep_common_f",         "value": "F=F&reg=SE",
                 "domain": ".aliexpress.com", "path": "/"},
                {"name": "_aep_modified_region", "value": "SE",
                 "domain": ".aliexpress.com", "path": "/"},
            ])

            try:
                print("   ⏳ Navigating...")
                page.goto(url, timeout=120_000, wait_until="domcontentloaded")
                time.sleep(3)

                if is_captcha_page(page):
                    print("   ⚠️ CAPTCHA — rotating and retrying...")
                    browser.close()
                    continue

                print("   ⏳ Waiting for page JS to render (10s)...")
                time.sleep(10)

                # Gentle scroll to trigger lazy rendering of SKU section
                for _ in range(4):
                    page.mouse.wheel(0, random.randint(200, 400))
                    time.sleep(random.uniform(0.3, 0.6))
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(2)

                if is_captcha_page(page):
                    print("   ⚠️ CAPTCHA after scroll — rotating and retrying...")
                    browser.close()
                    continue

                print(f"   ✓ Page title: {page.title()[:80]}")

                # Extract variants
                variants = _extract_variants(page)
                browser.close()

                if not variants:
                    print("   ⚠️ No variants found — product may have no options or page blocked")
                    # Don't retry — return empty success so we don't waste retries
                    # on genuinely single-variant products
                    return {
                        **base_result,
                        "variants":   {},
                        "success":    True,   # scrape succeeded, product just has no variants
                        "error":      None,
                        "scraped_at": datetime.utcnow().isoformat(),
                    }

                print(f"   ✅ Extracted {len(variants)} variant type(s):")
                for vtype, values in variants.items():
                    print(f"      • {vtype}: {values}")

                return {
                    **base_result,
                    "variants":   variants,
                    "success":    True,
                    "error":      None,
                    "scraped_at": datetime.utcnow().isoformat(),
                }

            except PlaywrightTimeoutError as e:
                print(f"   ⚠️ Timeout: {e}")
                try: browser.close()
                except Exception: pass

            except Exception as e:
                print(f"   ❌ Error: {e}")
                import traceback; traceback.print_exc()
                try: browser.close()
                except Exception: pass

    print(f"\n❌ Failed after {MAX_RETRIES} attempts")
    return {
        **base_result,
        "error":      f"Failed after {MAX_RETRIES} attempts",
        "scraped_at": datetime.utcnow().isoformat(),
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))
