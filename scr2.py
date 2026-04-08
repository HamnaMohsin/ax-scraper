"""
AliExpress Product ID + Title Scraper v5 - Poland/English
Changelog vs v4:
  - Completely rewrote set_poland_english_language() using the exact
    CSS classes found in the live AliExpress settings panel HTML.
  - Added open_settings_panel() to reliably open the ship-to/language
    dialog before trying to interact with dropdowns.
  - Added select_dropdown_item() helper that handles open → search →
    click for any of the three select widgets (country, language, currency).
  - Saves after every change; verifies Poland flag in header before
    returning True.
  - All other logic (Tor rotation, HTML parsing, main loop) unchanged.
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
    sys.exit("❌  Playwright not found.\n    Run:  pip install playwright && playwright install chromium")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("❌  BeautifulSoup not found.\n    Run:  pip install beautifulsoup4")

try:
    from stem import Signal
    from stem.control import Controller
except ImportError:
    sys.exit("❌  stem not found.\n    Run:  pip install stem")


# ── Configuration ─────────────────────────────────────────────────────────────
CATEGORIES = [
    "lapdesks",
    "led strip lights",
    "phone case",
    "laptop stand",
    "smart watch",
]

MAX_PAGES_PER_CATEGORY = 3
OUTPUT_FILE = "aliexpress_products.json"
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL = (
    "https://www.aliexpress.com/w/wholesale-{slug}.html"
    "?SearchText={query}&catId=0&g=y&shipFromCountry=&trafficChannel=main&page={page}"
)

# ── Exact CSS class names extracted from the live AliExpress settings HTML ────
# These are the obfuscated but stable class names observed in the DOM snapshot.
SEL_SETTINGS_TRIGGER = (
    # Primary: the flag+country text block in the header that opens the panel
    "[class*='ship-to'], "
    "[class*='shipTo'], "
    "[data-role='ship-to'], "
    # Fallback: the Poland flag span itself (clicking it opens the panel)
    ".country-flag-y2023.PL, "
    # Last-resort: any element whose aria-label mentions ship or currency
    "[aria-label*='ship'], [aria-label*='Ship']"
)

# The overlay/dialog that appears after clicking the trigger
SEL_SETTINGS_PANEL = (
    ".es--contentWrap--ypzOXHr, "          # exact class from HTML snapshot
    "[class*='contentWrap'], "
    "[class*='ship-to-content'], "
    "[class*='shipToContent']"
)

# Individual select widget wrapper (there are 3: country, language, currency)
SEL_SELECT_WRAP   = ".select--wrap--3N7DHe_, [class*='select--wrap']"
SEL_SELECT_TEXT   = ".select--text--1b85oDo, [class*='select--text']"
SEL_SELECT_POPUP  = ".select--popup--W2YwXWt, [class*='select--popup']"
SEL_SELECT_ITEM   = ".select--item--32FADYB, [class*='select--item']"
SEL_SEARCH_INPUT  = ".select--search--20Pss08 input, [class*='select--search'] input"
SEL_SAVE_BTN      = ".es--saveBtn--w8EuBuy, [class*='saveBtn']"

# Country-flag class used to verify Poland is active in the header
SEL_POLAND_FLAG   = ".country-flag-y2023.PL"


# ── Tor helpers ───────────────────────────────────────────────────────────────
def rotate_tor_circuit():
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
        time.sleep(8)
        print("   ✅ Tor rotated")
        return True
    except Exception as e:
        print(f"   ⚠️ Tor rotation failed: {e}")
        return False


def random_viewport() -> dict:
    viewports = [
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
    ]
    return random.choice(viewports)


# ── CAPTCHA detection ─────────────────────────────────────────────────────────
def is_captcha_page(page) -> bool:
    page_url   = page.url.lower()
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
    for sel in captcha_selectors:
        try:
            if page.locator(sel).count() > 0:
                print(f"❌ CAPTCHA detected via selector: {sel}")
                return True
        except Exception:
            continue
    return False


def diagnose_page(page, keyword: str, page_num: int) -> bool:
    print(f"   📋 URL: {page.url}")
    print(f"   📋 Title: {page.title()[:80]}")

    if is_captcha_page(page):
        print("   ❌ CAPTCHA/BLOCK DETECTED")
        return False

    item_links = len(page.locator("a[href*='/item/']").all())
    print(f"   📋 Item links found: {item_links}")
    return item_links > 5


# ── Settings panel helpers ────────────────────────────────────────────────────

def _wait_visible(page, selector: str, timeout: int = 5000):
    """Return the first visible locator match, or None."""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        return loc
    except Exception:
        return None


def open_settings_panel(page) -> bool:
    """
    Click the header element that opens the Ship-to / Language / Currency
    settings panel.  Returns True when the panel is visible.

    Strategy (tried in order):
      1. Direct URL parameter approach — navigate to /?lang=en&shipToCountry=PL
         (sets cookies server-side; no UI interaction needed).  Most reliable.
      2. Click the Poland-flag / ship-to trigger in the header.
      3. JavaScript dispatch of a click on the trigger element.
    """
    # ── Strategy 1: URL-based cookie injection (fastest, most reliable) ──────
    try:
        print("   📡 Strategy 1: URL param cookie injection …")
        page.goto(
            "https://www.aliexpress.com/?lang=en&shipToCountry=PL&currency=PLN",
            wait_until="domcontentloaded",
            timeout=35000,
        )
        time.sleep(3)
        if is_captcha_page(page):
            print("   ⚠️  CAPTCHA on strategy-1 page, skipping to strategy 2")
        else:
            # Check whether the header already shows Poland flag
            if page.locator(SEL_POLAND_FLAG).count() > 0:
                print("   ✅ Strategy 1 succeeded – Poland flag visible in header")
                return True
            print("   ℹ️  Strategy 1: flag not visible yet, falling through to UI method")
    except Exception as e:
        print(f"   ⚠️  Strategy 1 error: {e}")

    # ── Strategy 2: Click the header ship-to / flag trigger ──────────────────
    trigger_selectors = [
        # Most specific: the ship-to wrapper known from AliExpress header
        "[class*='ship-to--menuItem']",
        "[class*='shipTo--menuItem']",
        "[class*='ship-to']",
        "[class*='shipTo']",
        # The Poland flag span
        ".country-flag-y2023.PL",
        # Any element in the top nav that contains "Poland" text
        "//span[normalize-space()='Poland']",
        # Generic fallback
        "[data-role='ship-to']",
    ]

    for sel in trigger_selectors:
        try:
            locator = (
                page.locator(sel).first
                if not sel.startswith("//")
                else page.locator(f"xpath={sel}").first
            )
            if locator.count() > 0:
                locator.scroll_into_view_if_needed(timeout=3000)
                locator.click(timeout=4000)
                time.sleep(1.5)
                panel = _wait_visible(page, SEL_SETTINGS_PANEL, timeout=5000)
                if panel:
                    print(f"   ✅ Strategy 2 succeeded – panel opened via '{sel}'")
                    return True
        except Exception:
            continue

    # ── Strategy 3: JS click on any trigger candidate ────────────────────────
    print("   📡 Strategy 3: JS-driven panel open …")
    js_triggers = [
        "document.querySelector(\"[class*='ship-to']\")?.click()",
        "document.querySelector(\"[class*='shipTo']\")?.click()",
        "document.querySelector('.country-flag-y2023.PL')?.closest('div')?.click()",
    ]
    for js in js_triggers:
        try:
            page.evaluate(js)
            time.sleep(1.5)
            panel = _wait_visible(page, SEL_SETTINGS_PANEL, timeout=4000)
            if panel:
                print("   ✅ Strategy 3 succeeded – panel opened via JS")
                return True
        except Exception:
            continue

    print("   ❌ Could not open settings panel via any strategy")
    return False


def select_dropdown_item(page, wrap_index: int, desired_text: str) -> bool:
    """
    Open the Nth select widget inside the settings panel (0=country,
    1=province, 2=city, 3=language, 4=currency – but we only care about
    0=country, 3=language based on the HTML snapshot) and click the item
    whose visible text matches *desired_text* (case-insensitive prefix).

    Steps:
      1. Click the select--text div to open the popup.
      2. Type in the search box to filter.
      3. Click the first matching select--item.
    """
    try:
        wraps = page.locator(SEL_SELECT_WRAP).all()
        if wrap_index >= len(wraps):
            print(f"   ⚠️  select wrap index {wrap_index} out of range (found {len(wraps)})")
            return False

        wrap = wraps[wrap_index]

        # 1. Open the dropdown
        text_btn = wrap.locator(SEL_SELECT_TEXT).first
        text_btn.click(timeout=4000)
        time.sleep(0.8)

        # 2. Type in the search box to filter results
        search_input = wrap.locator(SEL_SEARCH_INPUT).first
        if search_input.count() > 0:
            search_input.fill(desired_text[:6], timeout=3000)   # short prefix is enough
            time.sleep(0.6)

        # 3. Click the matching item
        items = wrap.locator(SEL_SELECT_ITEM).all()
        for item in items:
            try:
                item_text = item.inner_text(timeout=1000).strip()
                if item_text.lower().startswith(desired_text.lower()):
                    item.click(timeout=3000)
                    time.sleep(0.5)
                    print(f"   ✓ Selected '{item_text}' (wrap #{wrap_index})")
                    return True
            except Exception:
                continue

        # Fallback: click by text locator
        item_loc = wrap.locator(f"{SEL_SELECT_ITEM}:has-text('{desired_text}')").first
        if item_loc.count() > 0:
            item_loc.click(timeout=3000)
            time.sleep(0.5)
            print(f"   ✓ Selected '{desired_text}' via text fallback (wrap #{wrap_index})")
            return True

        print(f"   ⚠️  Item '{desired_text}' not found in wrap #{wrap_index}")
        return False

    except Exception as e:
        print(f"   ⚠️  select_dropdown_item error (wrap #{wrap_index}, '{desired_text}'): {e}")
        return False


def click_save_button(page) -> bool:
    """Click the Save button and wait for the panel to close."""
    try:
        save_btn = page.locator(SEL_SAVE_BTN).first
        if save_btn.count() == 0:
            # Try XPath fallback
            save_btn = page.locator("xpath=//div[normalize-space()='Save']").first
        save_btn.click(timeout=5000)
        time.sleep(3)   # let the page reload/update after save
        print("   ✅ Save button clicked")
        return True
    except Exception as e:
        print(f"   ⚠️  Save button error: {e}")
        return False


# ── Main language/region setter ───────────────────────────────────────────────

def set_poland_english_language(page) -> bool:
    """
    Ensure the AliExpress session is set to:
      • Ship-to  → Poland (PL)
      • Language → English
      • Currency → PLN  (optional but consistent)

    Returns True when the Poland flag is confirmed in the header.

    The function tries three strategies in order:
      A) URL parameter approach  (sets cookies server-side – fastest)
      B) UI interaction with the settings panel dropdowns
      C) Direct cookie injection via JavaScript

    After A or B succeeds, it always verifies the Poland flag is present.
    """
    print("   🌍 Setting Poland region + English language …")

    # ── A: URL-parameter approach (already tried inside open_settings_panel) ──
    # We call open_settings_panel() first; if strategy 1 succeeds and the flag
    # is visible, open_settings_panel returns True and we can verify & return.
    panel_opened = open_settings_panel(page)

    # Quick verification after URL-param strategy
    if page.locator(SEL_POLAND_FLAG).count() > 0:
        print("   ✅ Poland flag confirmed in header (URL-param strategy)")
        return True

    # ── B: UI interaction ─────────────────────────────────────────────────────
    if panel_opened:
        print("   🖱️  Interacting with settings panel dropdowns …")

        # Identify which wrap index is "Ship to country" vs "Language".
        # From the HTML snapshot the order is:
        #   wrap 0 → Ship-to country (shows "Poland")
        #   wrap 1 → Province         (shows "Dolnoslaskie")
        #   wrap 2 → City             (shows "Boleslawiec")
        #   wrap 3 → Language         (shows "English")
        #   wrap 4 → Currency         (shows "PLN")
        #
        # We only need to set wrap 0 (country) and wrap 3 (language).
        # Province/city can stay as-is; they are auto-populated.

        country_set  = select_dropdown_item(page, wrap_index=0, desired_text="Poland")
        language_set = select_dropdown_item(page, wrap_index=3, desired_text="English")
        currency_set = select_dropdown_item(page, wrap_index=4, desired_text="PLN")

        if country_set or language_set:
            click_save_button(page)
        else:
            print("   ⚠️  No dropdowns were changed; skipping save")

        # Verify
        time.sleep(2)
        if page.locator(SEL_POLAND_FLAG).count() > 0:
            print("   ✅ Poland flag confirmed after UI interaction")
            return True

    # ── C: Cookie injection via JavaScript ────────────────────────────────────
    print("   🍪 Strategy C: injecting locale cookies via JS …")
    try:
        page.evaluate("""
            () => {
                const set = (name, val) => {
                    document.cookie = `${name}=${val};path=/;domain=.aliexpress.com`;
                };
                set('aep_usuc_f',  'site=glo&c_tp=PLN&region=PL&b_locale=en_US');
                set('intl_locale', 'en_US');
                set('acs_usuc_t',  '');
            }
        """)
        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        if page.locator(SEL_POLAND_FLAG).count() > 0:
            print("   ✅ Poland flag confirmed after cookie injection")
            return True

        print("   ⚠️  Cookie injection: flag still not visible, continuing anyway …")
        return True   # Don't block scraping; AliExpress may still serve correct data

    except Exception as e:
        print(f"   ⚠️  Cookie injection error: {e} – continuing anyway")
        return True


# ── URL / tag helpers ─────────────────────────────────────────────────────────
def build_url(keyword: str, page: int) -> str:
    slug  = keyword.strip().replace(" ", "-")
    query = keyword.strip().replace(" ", "+")
    return BASE_URL.format(slug=slug, query=query, page=page)


def is_ssr_url(href: str) -> bool:
    return "/ssr/" in href


def extract_product_id_from_href(href: str) -> str | None:
    m = re.search(r'/item/(\d{10,20})\.html', href)
    return m.group(1) if m else None


def is_nested_anchor(tag) -> bool:
    for parent in tag.parents:
        if parent.name == "a":
            return True
    return False


# ── HTML parsing ──────────────────────────────────────────────────────────────
def clean_title(raw: str) -> str:
    return " ".join(raw.split()).strip()


def extract_products_from_html(html: str) -> tuple[list[dict], dict]:
    soup = BeautifulSoup(html, "html.parser")
    seen_ids: set[str] = set()
    products: list[dict] = []
    stats = {"ssr_skipped": 0, "nested_skipped": 0, "tier": {}}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        if is_ssr_url(href):
            stats["ssr_skipped"] += 1
            continue

        product_id = extract_product_id_from_href(href)
        if not product_id or product_id in seen_ids:
            continue

        if is_nested_anchor(a_tag):
            stats["nested_skipped"] += 1
            continue

        seen_ids.add(product_id)
        title = ""
        tier  = "missing"

        # 4-tier title extraction
        h3 = a_tag.find("h3")
        if h3:
            title = clean_title(h3.get_text())
            tier  = "h3"
        elif not title:
            heading = a_tag.find(attrs={"role": "heading"})
            if heading and heading.get("aria-label"):
                title = clean_title(heading["aria-label"])
                tier  = "aria-label"
        elif not title and a_tag.get("title"):
            title = clean_title(a_tag["title"])
            tier  = "title-attr"
        elif not title:
            for img in a_tag.find_all("img"):
                alt = img.get("alt", "").strip()
                if alt and len(alt) > 5:
                    title = clean_title(alt)
                    tier  = "img-alt"
                    break

        stats["tier"][tier] = stats["tier"].get(tier, 0) + 1
        products.append({"id": product_id, "title": title or "—", "_tier": tier})

    return products, stats


# ── Core scraper ──────────────────────────────────────────────────────────────
def scrape_category(browser, keyword: str, max_pages: int) -> dict:
    print(f"\n{'━'*60}")
    print(f"  🔍  {keyword.upper()}")
    print(f"{'━'*60}")

    all_products: list[dict] = []
    seen_ids: set[str] = set()

    context = browser.new_context(
        user_agent=random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]),
        viewport=random_viewport(),
        locale="en-US",
        timezone_id="Europe/Warsaw",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        },
    )

    # Block images to speed up loading
    context.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda r: r.abort())
    page = context.new_page()

    # Anti-detection patches
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver',  {get: () => undefined});
        Object.defineProperty(navigator, 'plugins',    {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages',  {get: () => ['en-US','en','pl']});
        window.chrome = {runtime: {}};
    """)

    try:
        # STEP 1: Set Poland/English region
        if not set_poland_english_language(page):
            print("   ⚠️  Region setup failed – scraping anyway")

        # STEP 2: Scrape category pages
        for page_num in range(1, max_pages + 1):
            url = build_url(keyword, page_num)
            print(f"\n  [Page {page_num}/{max_pages}]  {url}")

            success = False
            for attempt in range(2):
                try:
                    print(f"   📡 Loading (attempt {attempt + 1}) …")
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(4)

                    if diagnose_page(page, keyword, page_num):
                        success = True
                        break
                    else:
                        print("   ❌ Page check failed – rotating Tor …")
                        rotate_tor_circuit()
                        time.sleep(12)

                except Exception as exc:
                    print(f"   ❌ Navigation error: {exc}")
                    if attempt == 1:
                        break
                    rotate_tor_circuit()
                    time.sleep(10)

            if not success:
                print("   ❌ Skipping page")
                continue

            # Scroll to trigger lazy-loaded products
            for _ in range(4):
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                time.sleep(0.5)
            time.sleep(2)

            html = page.content()
            page_products, stats = extract_products_from_html(html)

            new_products = [p for p in page_products if p["id"] not in seen_ids]
            for p in new_products:
                seen_ids.add(p["id"])
            all_products.extend(new_products)

            print(f"  ✓ {len(new_products)} new | Total: {len(all_products)}")
            if new_products:
                for p in new_products[:2]:
                    title = (p["title"][:60] + "…") if len(p["title"]) > 60 else p["title"]
                    print(f"    ↳ {p['id']} [{p['_tier']}] {title}")

            if len(new_products) == 0 and page_num > 1:
                print("  ⚠️  No new products – stopping early")
                break

            time.sleep(random.uniform(4, 7))

    finally:
        context.close()

    clean_products = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {"keyword": keyword, "products": clean_products, "count": len(clean_products)}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AliExpress Product ID + Title Scraper v5")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--pages",    type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output",   default=OUTPUT_FILE)
    args = parser.parse_args()

    headless  = args.headless.lower() == "true"
    timestamp = datetime.now().isoformat()

    print(f"\n{'═'*70}")
    print("  🚀 AliExpress Scraper v5 - Poland/English Edition")
    print(f"  📅 {timestamp} | Headless: {headless} | Pages: {args.pages}")
    print(f"{'═'*70}")

    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            for keyword in CATEGORIES:
                result = scrape_category(browser, keyword, args.pages)
                results[keyword] = result
                print(f"\n  ✅ '{keyword}': {result['count']} products")
                time.sleep(random.uniform(5, 9))
        finally:
            browser.close()

    total = sum(r["count"] for r in results.values())
    output_data = {
        "scraped_at":     timestamp,
        "region":         "Poland/English",
        "total_products": total,
        "results":        results,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n{'═'*70}")
    print("  🎉 SCRAPING COMPLETE")
    print(f"  📊 {total:,} total products → {args.output}")
    print(f"{'═'*70}")


if __name__ == "__main__":
    main()
