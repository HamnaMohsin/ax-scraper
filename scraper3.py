"""
scr_variants.py — AliExpress Product Variant Scraper
=====================================================
Scrapes all variant types, values, and images from an AliExpress product page.
Uses Tor (socks5://127.0.0.1:9050) + plain Playwright (same pattern as main scraper).

Approach:
  - Per-attempt `with sync_playwright() as p` block (browser fully closed each retry)
  - No Camoufox dependency
  - No gatewayAdapt param (avoids .us redirect / login wall)
  - Detects aliexpress.us redirect explicitly
  - Smart reCAPTCHA detection (size + visibility check, not just presence)
  - Waits for SKU selector instead of fixed sleep

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

# Minimal cookies — locale only, no region/currency that could mismatch Tor exit IP
COOKIES = [
    {"name": "intl_locale", "value": "en_US",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "aep_usuc_f",  "value": "site=glo&c_tp=USD&region=US&b_locale=en_US",
     "domain": ".aliexpress.com", "path": "/"},
]

# SKU selector — wait for React variant widgets to finish rendering
SKU_SELECTOR = '[data-sku-col], [class*="sku-item"], [class*="sku--wrap"]'

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_tor_ip() -> str:
    """Return current exit IP via Tor socks proxy. Used to verify rotation."""
    try:
        import urllib.request
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({
                "http":  "socks5h://127.0.0.1:9050",
                "https": "socks5h://127.0.0.1:9050",
            })
        )
        with opener.open("https://api.ipify.org", timeout=15) as r:
            return r.read().decode().strip()
    except Exception as e:
        return f"unknown ({e})"


def rotate_tor_circuit() -> bool:
    ip_before = get_tor_ip()
    print(f"   Current exit IP: {ip_before}")
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()  # null auth — no password needed
            ctrl.signal(Signal.NEWNYM)
            print("   Waiting 20s for new Tor circuit...")
            for i in range(20):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {20 - i - 1}s remaining")
        ip_after = get_tor_ip()
        print(f"   New exit IP    : {ip_after}")
        if ip_before == ip_after:
            print("⚠️  WARNING: IP did not change — try adding more ExitNodes to torrc")
        else:
            print("✅ Tor circuit rotated — IP changed")
        return True
    except Exception as e:
        print(f"⚠️  Could not rotate Tor circuit: {e}")
        return False


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


# ── Detection helpers ─────────────────────────────────────────────────────────

def is_captcha_page(page) -> bool:
    """
    Detect genuine block/CAPTCHA pages.

    reCAPTCHA iframes also appear on normal AliExpress pages (login score pixels).
    Only flag as blocked if the iframe is VISIBLE and wider than 200px.
    """
    url   = page.url.lower()
    title = page.title().lower()

    # Hard URL signals
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

    # reCAPTCHA: only flag if large AND visible (small = bot-score pixel, not a challenge)
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


def is_bad_redirect(page) -> bool:
    """
    Detect redirects away from the product page.
    Catches both homepage redirects and aliexpress.us (login wall).
    """
    url = page.url
    if "aliexpress.us" in url:
        # US storefront forces login wall / aggressive CAPTCHA on every visit.
        # Fix: remove {US} from ExitNodes in torrc and restart Tor.
        print(f"❌ Redirected to aliexpress.us (US storefront — remove US from torrc ExitNodes). URL: {url}")
        return True
    if "/item/" not in url:
        print(f"❌ Not a product page. URL: {url}")
        return True
    return False


# ── Variant extraction JS ─────────────────────────────────────────────────────

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

            // Label detection — walk up DOM to find sku-item--property ancestor
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

            // Value + image collection
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


# ── Main ──────────────────────────────────────────────────────────────────────

def scrape_product_variants(product_id: int | str) -> dict:
    pid = int(product_id)
    # No gatewayAdapt — avoids forced storefront redirects
    url = f"https://www.aliexpress.com/item/{pid}.html"

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
            # Rotation already fired inline at end of previous attempt.
            # Just add a short settling buffer here before launching the browser.
            wait_time = 10 + (attempt * 5)
            print(f"   Settling {wait_time}s before launching browser on new circuit...")
            time.sleep(wait_time)

        # Fresh playwright + browser per attempt — same pattern as main scraper
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
            # Set cookies after page context is ready
            page.context.add_cookies(COOKIES)

            try:
                print("📡 Loading page...")
                page.goto(url, timeout=120_000, wait_until="domcontentloaded")
                time.sleep(2)
                print(f"   ✓ Landed: {page.url}")

                if is_bad_redirect(page):
                    browser.close()
                    rotate_tor_circuit()
                    time.sleep(15)
                    continue

                if is_captcha_page(page):
                    print("⚠️  CAPTCHA detected — closing browser and rotating IP...")
                    browser.close()
                    rotate_tor_circuit()
                    print("   Waiting 15s after rotation before next attempt...")
                    time.sleep(15)
                    continue

                # Wait for SKU widgets to render (React renders async after domcontentloaded)
                print("   ⏳ Waiting for SKU selector...")
                try:
                    page.wait_for_selector(SKU_SELECTOR, timeout=20_000)
                    print("   ✓ SKU selector found")
                    time.sleep(2)
                except PlaywrightTimeoutError:
                    print("   ⚠️  SKU selector not found — may be single-SKU, continuing...")

                # Gentle scroll to trigger lazy SKU rendering
                print("   ⏳ Scrolling to load all variants...")
                for _ in range(4):
                    page.mouse.wheel(0, random.randint(200, 400))
                    time.sleep(random.uniform(0.3, 0.6))
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(2)

                if is_bad_redirect(page):
                    browser.close()
                    rotate_tor_circuit()
                    time.sleep(15)
                    continue

                if is_captcha_page(page):
                    print("⚠️  CAPTCHA after scroll — rotating IP and retrying...")
                    browser.close()
                    continue

                title = page.title()
                print(f"   ✓ Title: {title[:80]}")
                if not title.strip():
                    print("⚠️  Empty title — page may not have loaded correctly")
                    browser.close()
                    continue

                variants = _extract_variants(page)
                browser.close()

                # Debug: print page snippet when nothing extracted
                if not variants:
                    print("   ⚠️  No variants found — product may be single-SKU")
                    # Uncomment below to debug blank/unexpected pages:
                    # snippet = page.content()[:800].replace("\n", " ").strip()
                    # print(f"   🔍 DEBUG page snippet: {snippet}")
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

                # Flatten: {"Color": {"values": [...], "images": [...]}} → {"Color": "v1,v2,v3"}
                flat = {
                    group: ",".join(data["values"]) if isinstance(data, dict) else data
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


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "1005011748833056"
    result = scrape_product_variants(pid)
    print("\n" + json.dumps(result, indent=2))        except:
            print("   ⚠️ Modal did not appear after click")
            return compliance

        modal = page.locator(modal_selector).first
        if modal.count() == 0:
            print("   ⚠️ Modal body not found")
            return compliance

        raw_text = modal.inner_text().strip()
        print(f"   ✓ Modal text ({len(raw_text)} chars):\n      {raw_text[:300]}")

        # Step 3: Parse sections from raw text
        # Sections we look for as keys
        section_headers = [
            "Manufacturer information",
            "EU responsible person information",
            "Product identifier",
        ]

        # Split text into labelled sections
        # Strategy: walk line by line, detect section headers, collect their content
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

        current_section = None
        section_lines: dict[str, list[str]] = {}

        for line in lines:
            # Check if this line is a section header
            matched_header = next(
                (h for h in section_headers if line.lower().startswith(h.lower())),
                None
            )
            if matched_header:
                current_section = matched_header
                section_lines[current_section] = []
                # If there's content on the same line after the header, keep it
                remainder = line[len(matched_header):].strip().lstrip(":").strip()
                if remainder:
                    section_lines[current_section].append(remainder)
            elif current_section:
                section_lines[current_section].append(line)

        # Step 4: Parse key:value pairs inside each section
        def parse_kv_block(lines_list: list[str]) -> dict:
            """Parse lines like 'Name:Foo', 'Address:Bar', etc."""
            result = {}
            for l in lines_list:
                if ":" in l:
                    # Split only on first colon to handle addresses with colons
                    k, _, v = l.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k and v and len(k) < 60:
                        result[k] = v
                else:
                    # Plain text line with no colon — store as raw value
                    # (e.g. product identifier is just a number string)
                    if l and not result.get("value"):
                        result["value"] = l
            return result

        for section, s_lines in section_lines.items():
            parsed = parse_kv_block(s_lines)
            if parsed:
                compliance[section] = parsed
                print(f"   ✅ {section}: {parsed}")

        # Step 5: Close modal so it doesn't interfere with later extraction
        close_selectors = [
            "button.comet-v2-modal-close",
            "[class*='modal-close']",
            "[aria-label='Close']",
        ]
        for sel in close_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_timeout(500)
                    print("   ✓ Closed compliance modal")
                    break
            except:
                continue

    except Exception as e:
        print(f"⚠️ Compliance extraction error: {e}")
        import traceback
        traceback.print_exc()

    return compliance

def clean_text(text: str) -> str:
    """Clean and normalize text"""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds: float = 1, max_seconds: float = 3):
    """Random delay to mimic human behavior"""
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def random_viewport():
    """Return random viewport size"""
    viewports = [
        {'width': 1366, 'height': 768},
        {'width': 1920, 'height': 1080},
        {'width': 1440, 'height': 900},
        {'width': 1280, 'height': 720},
    ]
    return random.choice(viewports)


def rotate_tor_circuit():
    """Rotate Tor circuit to get new exit IP - wait longer for actual change"""
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("   Waiting 15s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {15 - i - 1}s remaining")
        print("✅ Tor circuit rotated - new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


def is_captcha_page(page) -> bool:
    """Detect if page is a CAPTCHA/block page - multiple selectors"""
    page_url = page.url.lower()
    page_title = page.title().lower()

    captcha_url_keywords = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
    if any(kw in page_url for kw in captcha_url_keywords):
        print("❌ CAPTCHA detected in URL")
        return True

    captcha_selectors = [
        "iframe[src*='recaptcha']",
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha']",
        "iframe[src*='geetest']",
        "[class*='captcha']",
    ]

    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA detected: {selector}")
                return True
        except:
            continue

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    block_title_keywords = ["verify", "access", "denied", "blocked", "challenge"]
    if not is_product_page and any(kw in page_title for kw in block_title_keywords):
        print("❌ Block page detected from title")
        return True

    return False



def extract_store_info_universal(page) -> dict:
    """Extract store info by hovering over the store element to trigger the popup."""
    store_info = {}
 
    print("📦 Extracting store info...")
 
    try:
        # Step 1: Extract store name directly from known selector (always visible)
        print("   🔍 Step 1: Extracting store name...")
        store_name_selector = "span[class*='store-detail--storeName']"
        store_name_elem = page.locator(store_name_selector).first
 
        if store_name_elem.count() > 0:
            store_name = store_name_elem.inner_text().strip()
            if store_name:
                store_info["Store Name"] = store_name
                print(f"   ✓ Store name: {store_name}")
        else:
            print("   ⚠️ Store name element not found")
 
        # Step 2: Hover over the store link to trigger the popup
        print("   🔍 Step 2: Hovering to reveal store detail popup...")
        store_link_selector = "div[class*='store-detail--storeNameWrap']"
        store_link_elem = page.locator(store_link_selector).first
 
        if store_link_elem.count() > 0:
            store_link_elem.hover()
            page.wait_for_timeout(1500)
            print("   ✓ Hovered over store element")
        else:
            print("   ⚠️ Store link element not found, skipping hover")
 
        # Step 3: Extract all key-value rows from the popup (renders after hover)
        print("   🔍 Step 3: Extracting popup store details...")
 
        row_selectors = [
            "div[class*='store-detail'] table tr",
            "div[class*='storeDetail'] table tr",
            "[class*='store-detail--detail'] tr",
        ]
 
        for row_selector in row_selectors:
            rows = page.locator(row_selector).all()
            if rows:
                print(f"   ✓ Found {len(rows)} rows with: {row_selector}")
                for row in rows:
                    try:
                        cols = row.locator('td').all()
                        if len(cols) >= 2:
                            key = cols[0].inner_text().strip().replace(":", "")
                            value = cols[1].inner_text().strip()
                            if key and value:
                                store_info[key] = value
                                print(f"      {key}: {value}")
                    except:
                        continue
                if len(store_info) > 1:
                    break
 
        # Step 4: Fallback — read visible popup text and parse key: value lines
        if len(store_info) <= 1:
            print("   🔍 Step 4: Fallback — reading popup text directly...")
            popup_selectors = [
                "div[class*='store-detail--storePopup']",
                "div[class*='store-detail--popup']",
                "div[class*='storePopup']",
                "div[class*='store-detail']:not(a)",
            ]
 
            for popup_selector in popup_selectors:
                popup = page.locator(popup_selector).first
                if popup.count() > 0:
                    text = popup.inner_text().strip()
                    if text:
                        print(f"   ✓ Popup text ({popup_selector}):\n      {text[:200]}")
                        for line in text.split('\n'):
                            line = line.strip()
                            if ':' in line:
                                parts = line.split(':', 1)
                                key = parts[0].strip()
                                value = parts[1].strip()
                                if key and value and len(key) < 50:
                                    store_info[key] = value
                                    print(f"      {key}: {value}")
                    if len(store_info) > 1:
                        break
 
        if not store_info:
            print("   ⚠️ Could not extract store information")
        else:
            print(f"   ✅ Store info extracted: {store_info}")
 
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
        import traceback
        traceback.print_exc()
 
    return store_info


def extract_title_universal(page) -> str:
    """Extract title - try multiple selectors"""

    print("📌 Extracting title...")

    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        ('h1', "h1 heading"),
        ('[class*="product-title"]', "product-title class"),
        ('[class*="ProductTitle"]', "ProductTitle class"),
        ('span[class*="title"]', "span title class"),
    ]

    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 10:
                    print(f"✅ Title ({desc}): {title[:80]}...")
                    return title
        except:
            continue

    print("⚠️ Could not extract title")
    return ""

def extract_specifications(page) -> dict:
    """
    Extract product specifications from the #nav-specification section.
    Clicks 'View more' first to expand the full list.
    """
    specifications = {}
    print("📋 Extracting specifications...")

    try:
        # ── Step 1: scroll section into view and wait for initial render ──
        spec_section = page.locator("#nav-specification")
        if spec_section.count() == 0:
            print("   ⚠️ #nav-specification not found")
            return specifications

        spec_section.scroll_into_view_if_needed()
        page.wait_for_timeout(2500)

        # ── Step 2: click "View more" button if present ──
        # Selector targets the button directly inside #nav-specification
        view_more_sel = "#nav-specification > button"
        try:
            view_more_btn = page.locator(view_more_sel).first
            if view_more_btn.count() > 0:
                print("   🔽 'View more' button found — clicking...")
                view_more_btn.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                view_more_btn.click(timeout=5000)
                page.wait_for_timeout(2000)   # wait for hidden rows to render
                print("   ✓ 'View more' clicked — full spec list should be visible")
            else:
                print("   ℹ️ No 'View more' button — spec list already fully expanded")
        except Exception as btn_err:
            print(f"   ⚠️ Could not click 'View more' (non-fatal): {btn_err}")

        # ── Step 3: slow-scroll through the section to trigger lazy items ──
        try:
            box = spec_section.bounding_box()
            if box:
                bottom = box["y"] + box["height"]
                current = box["y"]
                while current < bottom:
                    page.mouse.wheel(0, 300)
                    page.wait_for_timeout(400)
                    current += 300
                page.evaluate(
                    "el => el.scrollIntoView({block:'start'})",
                    spec_section.element_handle()
                )
                page.wait_for_timeout(1000)
        except Exception as scroll_err:
            print(f"   ⚠️ Scroll-through error (non-fatal): {scroll_err}")

        # ── Step 4: locate <li> rows ──
        li_selector = "#nav-specification ul li"
        spec_items = page.locator(li_selector).all()

        if not spec_items:
            print("   ⚠️ No <li> items found inside #nav-specification ul")
            return specifications

        print(f"   ✓ Found {len(spec_items)} spec <li> rows")

        # ── Step 5: extract key/value pairs — three-strategy fallback ──
        prop_sel  = "[class*='specification--prop']"
        title_sel = "[class*='specification--title'] span, [class*='specTitle'] span"
        desc_sel  = "[class*='specification--desc'] span, [class*='specValue'] span"

        for idx, item in enumerate(spec_items):
            try:
                props = item.locator(prop_sel).all()

                if props:
                    # Strategy A – structured prop containers
                    for prop in props:
                        try:
                            t_el = prop.locator(title_sel).first
                            d_el = prop.locator(desc_sel).first

                            key = t_el.inner_text(timeout=3000).strip() if t_el.count() > 0 else ""
                            val = d_el.inner_text(timeout=3000).strip() if d_el.count() > 0 else ""

                            if key and val:
                                specifications[key] = val
                                print(f"      [A] {key}: {val}")
                        except Exception:
                            continue

                else:
                    # Strategy B – flat row: two sibling spans
                    spans = item.locator("span").all()
                    if len(spans) >= 2:
                        try:
                            key = spans[0].inner_text(timeout=2000).strip()
                            val = spans[1].inner_text(timeout=2000).strip()
                            if key and val:
                                specifications[key] = val
                                print(f"      [B] {key}: {val}")
                            continue
                        except Exception:
                            pass

                    # Strategy C – raw text split
                    try:
                        raw = item.inner_text(timeout=2000).strip()
                        lines = [l.strip() for l in raw.splitlines() if l.strip()]
                        if len(lines) >= 2:
                            key, val = lines[0], lines[1]
                            if key and val:
                                specifications[key] = val
                                print(f"      [C] {key}: {val}")
                        elif len(lines) == 1 and ":" in lines[0]:
                            k, _, v = lines[0].partition(":")
                            if k.strip() and v.strip():
                                specifications[k.strip()] = v.strip()
                                print(f"      [C:] {k.strip()}: {v.strip()}")
                    except Exception:
                        continue

            except Exception as row_err:
                print(f"   ⚠️ Row {idx} error: {row_err}")
                continue

        print(f"   ✅ Specifications extracted: {len(specifications)} fields")

    except Exception as e:
        print(f"⚠️ Specification extraction error: {e}")
        import traceback
        traceback.print_exc()

    return specifications

def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing and anti-detection.
    """
    if "gatewayAdapt" in url:
        # Replace whatever gateway is there with glo2swe
        url = re.sub(r'gatewayAdapt=[^&]+', 'gatewayAdapt=glo2swe', url)
    else:
        # Append it
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}gatewayAdapt=glo2swe"

  

    print(f"\n🔍 Scraping: {url}")

    empty_result = {
    "title": "",
    "description_text": "",
    "images": [],
    "store_info": {},
    "compliance_info": {},
    "specifications": {},
    }

    max_retries = 3

    for attempt in range(max_retries):
        print(f"\n📍 Attempt {attempt + 1}/{max_retries}")

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
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )

            page = browser.new_page(
                viewport=random_viewport(),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]),
                timezone_id=random.choice([
                    'America/New_York',
                    'America/Chicago',
                    'America/Denver',
                    'America/Los_Angeles',
                ])
            )

            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")

            try:
                # NAVIGATION
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)

                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Redirected to: {current_url}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue

                print("⏳ Waiting for page to render...")
                time.sleep(8)

                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)
                
                # EXTRACT COMPLIANCE INFO
                compliance_info = extract_compliance_info(page)
                

                # EXTRACT DESCRIPTION
                print("📝 Loading description...")
                description_text = ""
                description_images = []

                try:
                    # Click description tab
                    print("   Clicking Description tab...")
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)

                        buttons = page.locator('a.comet-v2-anchor-link').all()
                        for btn in buttons:
                            if 'description' in btn.inner_text().strip().lower():
                                print("   ✓ Found Description button (comet-v2-anchor-link)")
                                btn.click(force=True, timeout=2000)
                                print("   ⏳ Waiting for description content to load...")
                                page.wait_for_timeout(3000)
                                try:
                                    page.locator('#product-description').scroll_into_view_if_needed()
                                    page.wait_for_timeout(2000)
                                except:
                                    pass
                                page.wait_for_timeout(3000)
                                print("   ✓ Clicked Description tab")
                                break
                    except Exception as e:
                        print(f"   ⚠️ Description tab click error: {e}")

                    # METHOD 0: Extract all <p> tags inside #product-description
                    print("   🎯 Method 0: Extracting paragraph text...")
                    method0_text = ""
                    try:
                        all_paragraphs = page.locator('#product-description p').all()
                        all_text_parts = []
                        for p in all_paragraphs:
                            try:
                                txt = p.inner_text(timeout=2000).strip()
                                if txt and len(txt) > 2:
                                    all_text_parts.append(txt)
                            except:
                                pass
                        if all_text_parts:
                            method0_text = ' '.join(all_text_parts)
                            method0_text = re.sub(r'\s+', ' ', method0_text).strip()
                            print(f"   ✓ Method 0: {len(method0_text)} chars")
                        else:
                            print("   ⚠️ Method 0: no <p> content found")
                    except Exception as e:
                        print(f"   ⚠️ Method 0 failed: {e}")

                    # METHOD 1: inner_text() on full container
                    desc_container = page.locator('#product-description').first

                    if desc_container.count() > 0:
                        print("   ✓ Found #product-description container")
                        print("   🎯 Method 1: inner_text() on container...")

                        method1_text = desc_container.inner_text(timeout=5000).strip()
                        method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                        print(f"   ✓ Method 1: {len(method1_text)} chars")

                        # Retry once if too short
                        if len(method1_text) < 100:
                            print("   ⏳ Content short, waiting 5s and retrying...")
                            page.wait_for_timeout(5000)
                            method1_text = desc_container.inner_text(timeout=5000).strip()
                            method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                            print(f"   ✓ Method 1 after retry: {len(method1_text)} chars")

                        # CONCATENATE: Method 0 + Method 1
                        parts = [t for t in [method0_text, method1_text] if t]
                        description_text = ' '.join(parts)
                        description_text = re.sub(r'\s+', ' ', description_text).strip()
                        print(f"   ✅ Combined (Method 0 + Method 1): {len(description_text)} chars")

                        # IMAGE EXTRACTION: direct locator on container
                        print("   🖼️ Extracting images...")
                        all_imgs = desc_container.locator('img').all()
                        print(f"      Found {len(all_imgs)} <img> tags")

                        for img in all_imgs:
                            src = (img.get_attribute("src") or
                                   img.get_attribute("data-src") or
                                   img.get_attribute("data-lazy-src"))
                            if src and "alicdn.com" in src:
                                clean_src = src.split('?')[0]
                                if clean_src not in description_images:
                                    description_images.append(clean_src)

                        # Quality filter + limit
                        description_images = [
                            img for img in description_images
                            if len(img) > 50 and not any(
                                bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100']
                            )
                        ][:20]
                        print(f"   ✓ Images: {len(description_images)}")

                        if description_images:
                            for i, img_url in enumerate(description_images[:3], 1):
                                print(f"      {i}. {img_url[:60]}...")
                    else:
                        print("   ❌ #product-description not found")

                except Exception as e:
                    print(f"⚠️ Description extraction error: {e}")

                # SUCCESS
                specifications = extract_specifications(page)

                browser.close()

                
                result = {
                    "title":            title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info if isinstance(store_info, dict) else {},
                    "compliance_info":  compliance_info if isinstance(compliance_info, dict) else {},  # ← add
                    "specifications":   specifications if isinstance(specifications, dict) else {},

                }
                print(f"   compliance_info: {result['compliance_info']}")

                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title: {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images: {len(result['images'])} images")
                print(f"   store_info: {result['store_info']}")
                print(f"✅ Extraction successful on attempt {attempt + 1}\n")
                return result

            except PlaywrightTimeoutError as e:
                print(f"⚠️ Timeout on attempt {attempt + 1}: {e}")
                browser.close()
                continue

            except Exception as e:
                print(f"❌ Error on attempt {attempt + 1}: {e}")
                import traceback
                traceback.print_exc()
                try:
                    browser.close()
                except:
                    pass
                continue

    print(f"❌ Failed after {max_retries} attempts")
    return empty_result
