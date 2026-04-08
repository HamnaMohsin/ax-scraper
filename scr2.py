"""
AliExpress Product ID + Title Scraper
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Searches N categories on AliExpress, paginates through results,
and collects { product_id, title } pairs.

Fix v2: skips nested <a> mini-cards ("Similar items" / m5_* cards)
        that had empty alt text and no <h3>, causing ~5-12 missing
        titles per category at the end of the extracted list.

SSR/deal URLs (aliexpress.com/ssr/...) are also skipped for now.

v4: Added Poland/English language selection before scraping.

Requirements:
    pip install playwright beautifulsoup4 stem
    playwright install chromium
    # Tor must be running with ControlPort 9051 enabled

Usage:
    python aliexpress_scraper.py
    python aliexpress_scraper.py --headless false   # watch the browser
    python aliexpress_scraper.py --pages 5          # 5 pages per category
    python aliexpress_scraper.py --output results.json
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


# ── Tor helpers ───────────────────────────────────────────────────────────────

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


def random_viewport() -> dict:
    """Return a random but realistic viewport size."""
    viewports = [
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
    ]
    return random.choice(viewports)


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
        except Exception:
            continue

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    block_title_keywords = ["verify", "access", "denied", "blocked", "challenge"]
    if not is_product_page and any(kw in page_title for kw in block_title_keywords):
        print("❌ Block page detected from title")
        return True

    return False


def diagnose_page(page, keyword: str, page_num: int):
    """Diagnose why no products are found"""
    print(f"   📋 URL: {page.url}")
    print(f"   📋 Title: {page.title()[:80]}...")
    
    if is_captcha_page(page):
        print("   ❌ CAPTCHA/BLOCK DETECTED")
        return False
    
    item_links = len(page.locator("a[href*='/item/']").all())
    products = len(page.locator("[class*='product'], [class*='item']").all())
    print(f"   📋 Item links: {item_links} | Product cards: {products}")
    
    return item_links > 5  # Need at least 5 item links


def set_poland_english_language(page):
    """Navigate to language selector and set Poland/English"""
    print("   🌍 Setting Poland/English language...")
    
    try:
        # Click the ship-to menu (Poland selector)
        ship_to_selector = ".ship-to--menuItem--WdBDsYl, [aria-label*='country'], [class*='ship-to']"
        ship_to = page.locator(ship_to_selector).first
        if ship_to.count() > 0:
            ship_to.click(timeout=5000)
            time.sleep(1)
            print("   ✓ Ship-to menu opened")
        else:
            print("   ⚠️ Ship-to menu not found, continuing...")
        
        # Look for Poland option and click it
        poland_selectors = [
            "[class*='ship-to'][class*='PL']",
            "text=Poland",
            "[data-country='PL']",
            ".country-flag-y2023.PL",
            "[aria-label*='Poland']"
        ]
        
        poland_clicked = False
        for selector in poland_selectors:
            try:
                poland_option = page.locator(selector).first
                if poland_option.count() > 0:
                    poland_option.click(timeout=3000)
                    time.sleep(2)
                    print("   ✓ Poland selected")
                    poland_clicked = True
                    break
            except:
                continue
        
        if not poland_clicked:
            print("   ⚠️ Poland option not found, trying English...")
        
        # Ensure English language (usually default after Poland)
        english_selectors = [
            "text=English",
            "[lang='en']",
            "[data-lang='en']"
        ]
        for selector in english_selectors:
            try:
                english_option = page.locator(selector).first
                if english_option.count() > 0:
                    english_option.click(timeout=3000)
                    time.sleep(2)
                    print("   ✓ English language confirmed")
                    break
            except:
                continue
        
        # Verify we're on main page and not blocked
        time.sleep(3)
        if is_captcha_page(page):
            return False
        
        print("   ✅ Language/region set successfully")
        return True
        
    except Exception as e:
        print(f"   ⚠️ Language setup error: {e}")
        return False


# ── URL / tag helpers ─────────────────────────────────────────────────────────

def build_url(keyword: str, page: int) -> str:
    slug  = keyword.strip().replace(" ", "-")
    query = keyword.strip().replace(" ", "+")
    return BASE_URL.format(slug=slug, query=query, page=page)


def is_ssr_url(href: str) -> bool:
    """
    SSR / deal URLs look like:
      https://www.aliexpress.com/ssr/300001493/welcomegiftspmpc?...productIds=...
    These open promotional landing pages, not standard product pages.
    Skipped for now.
    """
    return "/ssr/" in href


def extract_product_id_from_href(href: str) -> str | None:
    """Pull numeric ID from a /item/1005009675360531.html href."""
    m = re.search(r'/item/(\d{10,20})\.html', href)
    return m.group(1) if m else None


def is_nested_anchor(tag) -> bool:
    """
    Return True if this <a> is nested inside another <a>.
    """
    for parent in tag.parents:
        if parent.name == "a":
            return True
    return False


# ── HTML parsing ──────────────────────────────────────────────────────────────

def clean_title(raw: str) -> str:
    """Collapse whitespace and invisible chars."""
    return " ".join(raw.split()).strip()


def extract_products_from_html(html: str) -> tuple[list[dict], dict]:
    """
    Parse rendered HTML and return:
        (products, stats)
    """
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

        # Tier 1 — <h3> inside the card
        h3 = a_tag.find("h3")
        if h3:
            title = clean_title(h3.get_text())
            tier  = "h3"

        # Tier 2 — aria-label on role="heading" element
        if not title:
            heading = a_tag.find(attrs={"role": "heading"})
            if heading and heading.get("aria-label"):
                title = clean_title(heading["aria-label"])
                tier  = "aria-label"

        # Tier 3 — title attribute on the <a> itself
        if not title and a_tag.get("title"):
            title = clean_title(a_tag["title"])
            tier  = "title-attr"

        # Tier 4 — alt text of first non-trivial <img>
        if not title:
            for img in a_tag.find_all("img"):
                alt = img.get("alt", "").strip()
                if alt and len(alt) > 5:
                    title = clean_title(alt)
                    tier  = "img-alt"
                    break

        stats["tier"][tier] = stats["tier"].get(tier, 0) + 1
        products.append({"id": product_id, "title": title or "—", "_tier": tier})

    return products, stats


# ── Misc helpers ──────────────────────────────────────────────────────────────

def human_delay(lo: float = 1.5, hi: float = 3.5) -> None:
    time.sleep(random.uniform(lo, hi))


def slow_scroll(page, steps: int = 6) -> None:
    for _ in range(steps):
        page.evaluate("window.scrollBy(0, window.innerHeight * 0.75)")
        time.sleep(0.45)


# ── Core scraper ──────────────────────────────────────────────────────────────
def scrape_category(browser, keyword: str, max_pages: int) -> dict:
    print(f"\n{'━'*60}")
    print(f"  🔍  {keyword.upper()}")
    print(f"{'━'*60}")

    all_products: list[dict] = []
    seen_ids: set[str] = set()

    context = browser.new_context(
        user_agent=random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]),
        viewport=random_viewport(),
        locale="en-US",
        timezone_id="Europe/Warsaw",  # Poland timezone
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        },
    )

    context.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda r: r.abort())
    context.route("**/*{google-analytics,gtm,facebook,pixel}", lambda r: r.abort())

    page = context.new_page()
    
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en', 'pl']});
        window.chrome = {runtime: {}};
    """)

    try:
        # ── NEW: Set Poland/English first ──────────────────────────────────────
        print("\n  🌍 Initializing Poland/English region...")
        success = False
        for attempt in range(3):
            try:
                print(f"   📡 Loading AliExpress homepage (attempt {attempt+1})...")
                page.goto("https://www.aliexpress.com", wait_until="domcontentloaded", timeout=45_000)
                time.sleep(4)
                
                if set_poland_english_language(page):
                    success = True
                    break
                else:
                    print("   ❌ Language setup failed - rotating Tor...")
                    rotate_tor_circuit()
                    time.sleep(12)
                    
            except Exception as exc:
                print(f"   ❌ Homepage error: {exc}")
                if attempt == 2:
                    break
                rotate_tor_circuit()
                time.sleep(10)

        if not success:
            print("   ❌ Could not set Poland/English - continuing anyway...")

        # ── Now scrape category pages ──────────────────────────────────────────
        for page_num in range(1, max_pages + 1):
            url = build_url(keyword, page_num)
            print(f"\n  [Page {page_num}/{max_pages}]  {url}")

            success = False
            for attempt in range(2):
                try:
                    print(f"   📡 Loading (attempt {attempt+1})...")
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    time.sleep(4)

                    if diagnose_page(page, keyword, page_num):
                        success = True
                        break
                    else:
                        print("   ❌ Page failed - rotating Tor...")
                        rotate_tor_circuit()
                        time.sleep(12)

                except Exception as exc:
                    print(f"   ❌ Navigation error: {exc}")
                    if attempt == 1:
                        break
                    rotate_tor_circuit()
                    time.sleep(10)

            if not success:
                print("   ❌ All attempts failed - skipping page")
                continue

            slow_scroll(page)
            time.sleep(2)

            html = page.content()
            page_products, stats = extract_products_from_html(html)

            new_products = [p for p in page_products if p["id"] not in seen_ids]
            for p in new_products:
                seen_ids.add(p["id"])
            all_products.extend(new_products)

            print(f"  ✓ {len(page_products)} parsed | {len(new_products)} new | Total: {len(all_products)}")
            
            if new_products:
                for p in new_products[:2]:
                    title = (p["title"][:60] + "…") if len(p["title"]) > 60 else p["title"]
                    print(f"    ↳ {p['id']} [{p['_tier']}] {title}")

            if len(new_products) == 0 and page_num > 1:
                print("  ⚠️ No new products - stopping")
                break

            time.sleep(random.uniform(4, 7))

    finally:
        context.close()

    clean_products = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {"keyword": keyword, "products": clean_products, "count": len(clean_products)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AliExpress Product ID + Title Scraper")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--pages", type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    headless  = args.headless.lower() == "true"
    max_pages = args.pages
    output    = Path(args.output)
    timestamp = datetime.now().isoformat()

    print(f"\n{'═'*60}")
    print("  AliExpress Product Scraper  (ID + Title)  v4 - Poland/English")
    print(f"  Started   : {timestamp}")
    print(f"  Headless  : {headless}  |  Pages/category: {max_pages}")
    print(f"  Categories: {', '.join(CATEGORIES)}")
    print(f"{'═'*60}")

    results: dict[str, dict] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=[
                "--no-sandbox", 
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ],
        )
        try:
            for keyword in CATEGORIES:
                result = scrape
