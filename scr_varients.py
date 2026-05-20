"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Scrapes all variant types and values from an AliExpress product page.

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

try:
    from camoufox.sync_api import Camoufox
    USE_CAMOUFOX = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USE_CAMOUFOX = False

from playwright.sync_api import TimeoutError as PlaywrightTimeout

# ── Config ────────────────────────────────────────────────────────────────────

HEADLESS     = os.environ.get("HEADLESS", "1") != "0"
MAX_ATTEMPTS = 3
WAIT_SECS    = 8   # seconds to wait for page JS to hydrate

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ── Browser helpers ───────────────────────────────────────────────────────────

def _launch(url: str):
    """Launch browser and navigate to URL. Returns (cm, browser, ctx, page)."""
    cookies = [
        {"name": "aep_usuc_f", "value": "site=glo&c_tp=SEK&region=SE&b_locale=en_US",
         "domain": ".aliexpress.com", "path": "/"},
        {"name": "xman_us_f",  "value": "x_locale=en_US&x_site=SWE",
         "domain": ".aliexpress.com", "path": "/"},
    ]

    if USE_CAMOUFOX:
        cf = Camoufox(headless=HEADLESS, geoip=True, humanize=True)
        browser = cf.__enter__()
        ctx  = browser.new_context(locale="en-US")
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        return cf, browser, ctx, page
    else:
        pw      = sync_playwright().__enter__()
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        return pw, browser, ctx, page


def _close(cm, browser, ctx):
    for obj in (ctx, browser, cm):
        try:
            if hasattr(obj, "close"):       obj.close()
            elif hasattr(obj, "__exit__"):  obj.__exit__(None, None, None)
        except Exception:
            pass


# ── Variant extraction ────────────────────────────────────────────────────────

def _extract_variants(page) -> dict[str, str]:
    """
    Parse all SKU variant rows from the page.

    Strategy:
      1. Find every `.sku-item--skus--StEhULs` container (one per variant type).
      2. For each container, look for a label element ABOVE it in the DOM
         (AliExpress renders the label in a sibling/parent span).
      3. Collect all option values (alt text for images, span text for text options).
      4. Return { "Color": "black,white,pink", "Size": "S,M,L" }
    """
    return page.evaluate(r"""
    () => {
        const result = {};

        // Each SKU row container
        const rows = document.querySelectorAll('.sku-item--skus--StEhULs');

        rows.forEach((row, idx) => {
            // ── Detect variant type label ─────────────────────────────────
            // AliExpress puts the label in a span inside a sibling div
            // Walk up to the parent wrapper and search for a title/label element
            let label = null;

            // Try: parent's previous sibling contains the label span
            const parent = row.closest('[class*="sku-item--box"]') || row.parentElement;
            if (parent) {
                // Look for a span/div with the property name above the options
                const wrapper = parent.parentElement || parent;
                // Search all text nodes near this row for the label
                const labelCandidates = wrapper.querySelectorAll(
                    '[class*="sku-item--title"], [class*="sku-title"], ' +
                    '[class*="property-title"], [class*="variant-title"], ' +
                    '[class*="sku-item--property"], span[class*="title"]'
                );
                if (labelCandidates.length > 0) {
                    // Pick the one closest in DOM order
                    label = labelCandidates[0].textContent.trim().replace(/:$/, '');
                }
            }

            // Fallback: look for a sibling element containing a colon-terminated label
            if (!label) {
                const grandParent = row.parentElement && row.parentElement.parentElement;
                if (grandParent) {
                    for (const child of grandParent.children) {
                        const txt = child.textContent.trim();
                        // Short text ending with colon is usually a label
                        if (txt && txt.length < 40 && !child.querySelector('.sku-item--skus--StEhULs')) {
                            label = txt.replace(/:$/, '').trim();
                            break;
                        }
                    }
                }
            }

            // Final fallback: use generic name
            if (!label || label.length === 0) {
                label = `type_${idx + 1}`;
            }

            // ── Collect option values ─────────────────────────────────────
            const values = [];

            // Image-based options: use alt attribute
            const imgOptions = row.querySelectorAll('[class*="sku-item--image"] img');
            imgOptions.forEach(img => {
                const alt = (img.getAttribute('alt') || '').trim();
                if (alt && !values.includes(alt)) values.push(alt);
            });

            // Text-based options: use span text content
            if (values.length === 0) {
                const textOptions = row.querySelectorAll('[class*="sku-item--text"] span');
                textOptions.forEach(span => {
                    const txt = span.textContent.trim();
                    if (txt && !values.includes(txt)) values.push(txt);
                });
            }

            if (values.length > 0) {
                // If label already exists (duplicate row name), append index
                const key = result[label] !== undefined ? `${label}_${idx}` : label;
                result[key] = values.join(',');
            }
        });

        return result;
    }
    """)


def _wait_for_skus(page) -> bool:
    """Wait until SKU rows are rendered in the DOM."""
    try:
        page.wait_for_selector(
            ".sku-item--skus--StEhULs",
            timeout=15_000,
            state="attached",
        )
        return True
    except Exception:
        return False


# ── Main scraper function ─────────────────────────────────────────────────────

def scrape_product_variants(product_id: int | str) -> dict:
    """
    Scrape all variant types and values for a given AliExpress product ID.

    Returns:
        {
            "product_id":  <int>,
            "url":         <str>,
            "variants":    { "Color": "black,white", "Size": "S,M,L" },
            "success":     True/False,
            "error":       None or error message,
            "scraped_at":  ISO datetime string
        }
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

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n📍 Attempt {attempt}/{MAX_ATTEMPTS}")
        cm = browser = ctx = page = None

        try:
            cm, browser, ctx, page = _launch(url)

            print("   ⏳ Navigating...")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(random.uniform(2, 3))

            # Wait for SKU section to hydrate
            found = _wait_for_skus(page)
            if not found:
                print("   ⚠️  SKU rows not found — may be a single-variant product or page error")
                # Still try to extract — might load late
                time.sleep(WAIT_SECS)

            # Extra wait for JS rendering
            time.sleep(random.uniform(1, 2))

            variants = _extract_variants(page)

            if not variants:
                print("   ⚠️  No variants extracted — retrying...")
                _close(cm, browser, ctx)
                continue

            print(f"   ✅ Extracted {len(variants)} variant type(s):")
            for vtype, values in variants.items():
                print(f"      • {vtype}: {values}")

            _close(cm, browser, ctx)

            return {
                **base_result,
                "variants":   variants,
                "success":    True,
                "scraped_at": datetime.utcnow().isoformat(),
            }

        except PlaywrightTimeout as e:
            print(f"   ⚠️  Timeout: {e}")
            _close(cm, browser, ctx)

        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback; traceback.print_exc()
            _close(cm, browser, ctx)

    print(f"\n❌ Failed after {MAX_ATTEMPTS} attempts")
    return {
        **base_result,
        "error":      f"Failed after {MAX_ATTEMPTS} attempts",
        "scraped_at": datetime.utcnow().isoformat(),
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))
