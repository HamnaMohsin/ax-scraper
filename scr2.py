import argparse
import json
import re
import sys
import time
import random
import os
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install beautifulsoup4")

try:
    from stem import Signal
    from stem.control import Controller
except ImportError:
    sys.exit("pip install stem")


# ── Config ────────────────────────────────────────────────────────────────────
CATEGORIES = [
    "smart watch",
    "notebook",
    "dyson",
]
MAX_PAGES_PER_CATEGORY = 2
OUTPUT_FILE            = "aliexpress_products.json"
MAX_CAPTCHA_ROTATIONS  = 8
ROTATE_WAIT_SECS       = 14

BASE_URL = (
    "https://www.aliexpress.com/w/wholesale-{slug}.html"
    "?SearchText={query}&catId=0&g=y&page={page}"
    "&localeSite=pl&language=en&currencyCode=USD"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CAPTCHA_URL_TOKENS = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
CAPTCHA_SELECTORS  = [
    "iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
    "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']",
]
PRODUCT_CARD_SELECTOR = "a[href*='/item/']"

# ── Logging helpers ───────────────────────────────────────────────────────────
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log(emoji: str, msg: str, indent: int = 0) -> None:
    prefix = "  " * indent
    print(f"[{ts()}] {prefix}{emoji}  {msg}", flush=True)

def log_separator(char: str = "─", width: int = 68) -> None:
    print(char * width, flush=True)

# ── Tor ───────────────────────────────────────────────────────────────────────
def rotate_tor_circuit(wait: int = ROTATE_WAIT_SECS) -> bool:
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        log("🔄", f"Tor NEWNYM sent — waiting {wait}s …", indent=1)
        time.sleep(wait)
        return True
    except Exception as e:
        log("⚠️ ", f"Tor rotation failed: {e}", indent=1)
        return False

# ── Browser context / page factories ─────────────────────────────────────────
def make_context(browser):
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
    )
    # Set AliExpress locale cookies for Poland + English
    ctx.add_cookies([
        {"name": "aep_usuc_f",   "value": "site=pol&c_tp=USD&region=PL&b_locale=en_US", "domain": ".aliexpress.com", "path": "/"},
        {"name": "xman_us_f",    "value": "x_locale=en_US&x_site=POL",                  "domain": ".aliexpress.com", "path": "/"},
        {"name": "ali_apache_id","value": "PL",                                           "domain": ".aliexpress.com", "path": "/"},
    ])
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2}", lambda r: r.abort())
    return ctx

def make_page(context):
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}};
    """)
    return page

def is_captcha(page) -> bool:
    if any(t in page.url.lower() for t in CAPTCHA_URL_TOKENS):
        return True
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False

# ── Language / Region selector ────────────────────────────────────────────────
def select_poland_english(page) -> bool:
    """
    Clicks the ship-to widget, selects Poland as country and English as language,
    then confirms and waits for the page to reload.

    Returns True if the selection was applied, False if the widget was not found
    or the current settings are already correct (aria-label contains 'English').
    """
    try:
        # ── 1. Check whether we even need to act ─────────────────────────────
        ship_btn = page.locator(".ship-to--menuItem--WdBDsYl").first
        if ship_btn.count() == 0:
            log("⚙️ ", "Ship-to widget not found — skipping locale selection", indent=2)
            return False

        aria = ship_btn.get_attribute("aria-label") or ""
        if "English" in aria and ("PL" in aria or "Poland" in aria or "Polonia" in aria):
            log("⚙️ ", "Locale already Poland / English — no action needed", indent=2)
            return True

        log("⚙️ ", "Opening ship-to widget …", indent=2)

        # ── 2. Click the ship-to button ───────────────────────────────────────
        ship_btn.click()
        time.sleep(random.uniform(1.0, 1.8))

        # ── 3. Select Poland ──────────────────────────────────────────────────
        # The country dropdown items contain a <span class="country-flag-y2023 PL">
        # followed by a text node with the local name (e.g. "Polonia" or "Poland").
        # We target the wrapper that has both the PL flag span AND the select--text class.
        poland_selector = (
            ".select--text--1b85oDo:has(.country-flag-y2023.PL), "          # flag inside wrapper
            ".select--text--1b85oDo:has(span.PL)"                           # fallback
        )
        try:
            page.wait_for_selector(poland_selector, timeout=6000)
            poland_item = page.locator(poland_selector).first
            poland_item.scroll_into_view_if_needed()
            poland_item.click()
            log("🇵🇱", "Poland selected", indent=2)
            time.sleep(random.uniform(0.8, 1.4))
        except PlaywrightTimeout:
            log("⚠️ ", "Poland option not found in dropdown", indent=2)
            # Try pressing Escape to close the dialog before returning
            page.keyboard.press("Escape")
            return False

        # ── 4. Select English ─────────────────────────────────────────────────
        # The language items are plain .select--text--1b85oDo divs whose visible
        # text is exactly the language name.  We find the one whose inner text
        # contains "English".
        english_selector = ".select--text--1b85oDo"
        try:
            page.wait_for_selector(english_selector, timeout=6000)
            # Iterate visible items and click the one matching "English"
            items = page.locator(english_selector).all()
            clicked = False
            for item in items:
                try:
                    txt = (item.inner_text() or "").strip()
                    if txt.lower() == "english":
                        item.scroll_into_view_if_needed()
                        item.click()
                        log("🌐", "English selected", indent=2)
                        clicked = True
                        time.sleep(random.uniform(0.8, 1.4))
                        break
                except Exception:
                    continue
            if not clicked:
                log("⚠️ ", "English option not found — trying text search", indent=2)
                # Last-resort: use Playwright's text locator
                eng_loc = page.get_by_text("English", exact=True).first
                eng_loc.click()
                log("🌐", "English selected (text fallback)", indent=2)
                time.sleep(random.uniform(0.8, 1.4))
        except PlaywrightTimeout:
            log("⚠️ ", "Language options not found", indent=2)
            page.keyboard.press("Escape")
            return False

        # ── 5. Confirm / Save ─────────────────────────────────────────────────
        # AliExpress shows a "Save" or "Confirm" button in the modal.
        confirm_selectors = [
            "button.comet-btn-primary",                    # primary action button
            "button[class*='confirm']",
            "button[class*='save']",
            "[class*='ship-to--save']",
            "[class*='ship-to--confirm']",
            "button:has-text('Save')",
            "button:has-text('Confirm')",
        ]
        confirmed = False
        for sel in confirm_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    log("✅", f"Confirmed with selector: {sel}", indent=2)
                    confirmed = True
                    break
            except Exception:
                continue

        if not confirmed:
            log("⚠️ ", "No confirm button found — modal may auto-close", indent=2)

        # ── 6. Wait for page to settle after locale change ────────────────────
        log("⏳", "Waiting for page to reload after locale change …", indent=2)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightTimeout:
            pass
        time.sleep(random.uniform(2.5, 4.0))

        log("✅", "Locale selection complete (Poland / English)", indent=2)
        return True

    except Exception as exc:
        log("⚠️ ", f"select_poland_english() error: {exc}", indent=2)
        return False


# ── Page loading with rotation ────────────────────────────────────────────────
def load_page_with_rotation(browser, url: str):
    for attempt in range(MAX_CAPTCHA_ROTATIONS + 1):
        log("📡", f"Loading attempt {attempt + 1}/{MAX_CAPTCHA_ROTATIONS + 1} …", indent=1)
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector(PRODUCT_CARD_SELECTOR, timeout=12000)
            except PlaywrightTimeout:
                pass

            time.sleep(random.uniform(2, 4))

            if is_captcha(page):
                log("❌", "CAPTCHA detected — rotating …", indent=1)
                ctx.close()
                rotate_tor_circuit()
                continue

            # ── Language / region selection ───────────────────────────────────
            log("🌍", "Ensuring Poland / English locale …", indent=1)
            select_poland_english(page)

            # After locale change, check for captcha again
            if is_captcha(page):
                log("❌", "CAPTCHA after locale change — rotating …", indent=1)
                ctx.close()
                rotate_tor_circuit()
                continue

            item_count = page.locator(PRODUCT_CARD_SELECTOR).count()
            if item_count == 0:
                log("⚠️ ", "No products found — rotating …", indent=1)
                ctx.close()
                rotate_tor_circuit()
                continue

            log("✅", f"Page loaded OK — {item_count} items", indent=1)
            return page, ctx

        except Exception as exc:
            log("❌", f"Navigation error: {exc}", indent=1)
            ctx.close()
            rotate_tor_circuit()

    return None, None

# ── HTML parsing ──────────────────────────────────────────────────────────────
def clean_title(raw: str) -> str:
    return " ".join(raw.split()).strip()

def extract_products_from_html(html: str) -> tuple[list[dict], dict]:
    soup     = BeautifulSoup(html, "html.parser")
    seen_ids: set[str] = set()
    products: list[dict] = []
    stats    = {"ssr_skipped": 0, "tier": {}}
    id_re    = re.compile(r'/item/(\d{10,20})\.html')

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/ssr/" in href:
            stats["ssr_skipped"] += 1
            continue

        m = id_re.search(href)
        if not m: continue
        product_id = m.group(1)
        if product_id in seen_ids: continue

        seen_ids.add(product_id)
        title, tier = "", "missing"

        h3 = a_tag.find("h3")
        if h3:
            title, tier = clean_title(h3.get_text()), "h3"
        elif a_tag.get("title"):
            title, tier = clean_title(a_tag["title"]), "title-attr"
        else:
            for img in a_tag.find_all("img"):
                alt = img.get("alt", "").strip()
                if alt:
                    title, tier = clean_title(alt), "img-alt"
                    break

        stats["tier"][tier] = stats["tier"].get(tier, 0) + 1
        products.append({"id": product_id, "title": title or "—", "_tier": tier})

    return products, stats

# ── Category scraper ──────────────────────────────────────────────────────────
def scrape_category(browser, keyword: str, max_pages: int) -> dict:
    log_separator()
    log("🔍", f"CATEGORY: {keyword.upper()}")
    log_separator()

    all_products: list[dict] = []
    seen_ids: set[str] = set()

    for page_num in range(1, max_pages + 1):
        slug  = keyword.strip().replace(" ", "-")
        query = keyword.strip().replace(" ", "+")
        url   = BASE_URL.format(slug=slug, query=query, page=page_num)

        log("📄", f"[Page {page_num}/{max_pages}]")
        page, ctx = load_page_with_rotation(browser, url)

        if page is None:
            continue

        try:
            for i in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                time.sleep(0.3)

            html = page.content()
            page_products, stats = extract_products_from_html(html)
            new_products = [p for p in page_products if p["id"] not in seen_ids]
            for p in new_products: seen_ids.add(p["id"])
            all_products.extend(new_products)
            log("✓", f"Found {len(new_products)} new products", indent=1)
        finally:
            ctx.close()

        time.sleep(random.uniform(5, 10))

    clean = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {"keyword": keyword, "products": clean, "count": len(clean)}

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AliExpress Scraper v8.2")
    parser.add_argument("--headless",   default="true", choices=["true", "false"])
    parser.add_argument("--pages",      type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output",     default=OUTPUT_FILE)
    parser.add_argument("--categories", nargs="*", default=None)
    args = parser.parse_args()

    categories = args.categories or CATEGORIES
    headless   = args.headless.lower() == "true"

    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            for i, keyword in enumerate(categories, 1):
                log("🗂️ ", f"Category {i}/{len(categories)}: '{keyword}'")
                results[keyword] = scrape_category(browser, keyword, args.pages)
                if i < len(categories):
                    rotate_tor_circuit()
        finally:
            browser.close()

    # ── SAVE LOGIC: Merge instead of Overwrite ────────────────────────────────
    output_path = Path(args.output)
    existing_data = {"results": {}}

    if output_path.exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                log("💾", "Existing data found. Merging results...", indent=1)
        except Exception as e:
            log("⚠️ ", f"Could not read existing file, starting fresh: {e}")

    if "results" not in existing_data:
        existing_data["results"] = {}

    existing_data["results"].update(results)
    existing_data["last_updated"] = datetime.now().isoformat()
    existing_data["total_products"] = sum(r["count"] for r in existing_data["results"].values())

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)

    log_separator("═")
    log("🎉", f"COMPLETE. Saved to {args.output}")
    log("📊", f"Total products in file: {existing_data['total_products']}")
    log_separator("═")

if __name__ == "__main__":
    main()
