"""
AliExpress Product ID + Title Scraper v4 - Poland/English
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
    """Rotate Tor circuit"""
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
        time.sleep(8)
        print("   ✅ Tor rotated")
        return True
    except Exception as e:
        print(f"   ⚠️ Tor failed: {e}")
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


def is_captcha_page(page) -> bool:
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

    return False


def diagnose_page(page, keyword: str, page_num: int):
    print(f"   📋 URL: {page.url}")
    print(f"   📋 Title: {page.title()[:80]}...")
    
    if is_captcha_page(page):
        print("   ❌ CAPTCHA/BLOCK DETECTED")
        return False
    
    item_links = len(page.locator("a[href*='/item/']").all())
    print(f"   📋 Item links: {item_links}")
    
    return item_links > 5


def set_poland_english_language(page):
    """Set Poland region + ENGLISH language explicitly"""
    print("   🌍 Setting Poland region + English language...")
    
    try:
        # Go to English/Poland URL first (most reliable)
        print("   📡 Using direct URL for Poland/English...")
        page.goto("https://www.aliexpress.com/?lang=en&shipToCountry=PL", 
                 wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        
        # Verify we're not on CAPTCHA
        if is_captcha_page(page):
            print("   ❌ CAPTCHA after language set")
            return False
        
        # Double-check: Click language selector if needed
        lang_selectors = [
            "[aria-label*='language']",
            "[class*='lang']",
            ".site-language-selector"
        ]
        
        for selector in lang_selectors:
            try:
                lang_menu = page.locator(selector).first
                if lang_menu.count() > 0:
                    lang_menu.click(timeout=3000)
                    time.sleep(1)
                    
                    # Select English
                    english = page.locator("text=English, text=en").first
                    if english.count() > 0:
                        english.click(timeout=2000)
                        time.sleep(2)
                        print("   ✓ English language confirmed")
                    break
            except:
                continue
        
        # Verify Poland flag visible (from your HTML snippet)
        poland_flag = page.locator(".country-flag-y2023.PL, [class*='PL']").first
        if poland_flag.count() > 0:
            print("   ✓ Poland flag confirmed")
        
        # Check page shows English
        title = page.title().lower()
        if "english" in title or "en/" in page.content().lower():
            print("   ✅ Poland region + English language SET")
            return True
        else:
            print("   ⚠️ Language verification failed, continuing...")
            return True
            
    except Exception as e:
        print(f"   ⚠️ Language setup: {e} - continuing...")
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]),
        viewport=random_viewport(),
        locale="en-US",
        timezone_id="Europe/Warsaw",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        },
    )

    context.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda r: r.abort())
    page = context.new_page()
    
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en', 'pl']});
        window.chrome = {runtime: {}};
    """)

    try:
        # STEP 1: Set Poland/English region first
        set_poland_english_language(page)

        # STEP 2: Scrape category pages
        for page_num in range(1, max_pages + 1):
            url = build_url(keyword, page_num)
            print(f"\n  [Page {page_num}/{max_pages}]  {url}")

            success = False
            for attempt in range(2):
                try:
                    print(f"   📡 Loading (attempt {attempt+1})...")
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(4)

                    if diagnose_page(page, keyword, page_num):
                        success = True
                        break
                    else:
                        print("   ❌ Page failed - rotating...")
                        rotate_tor_circuit()
                        time.sleep(12)

                except Exception as exc:
                    print(f"   ❌ Error: {exc}")
                    if attempt == 1: break
                    rotate_tor_circuit()
                    time.sleep(10)

            if not success: 
                print("   ❌ Skipping page")
                continue

            # Scroll and extract
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
                print("  ⚠️ No new products - stopping")
                break

            time.sleep(random.uniform(4, 7))

    finally:
        context.close()

    clean_products = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {"keyword": keyword, "products": clean_products, "count": len(clean_products)}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AliExpress Product ID + Title Scraper v4")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--pages", type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    headless = args.headless.lower() == "true"
    timestamp = datetime.now().isoformat()

    print(f"\n{'═'*70}")
    print("  🚀 AliExpress Scraper v4 - Poland/English Edition")
    print(f"  📅 {timestamp} | Headless: {headless} | Pages: {args.pages}")
    print(f"{'═'*70}")

    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        try:
            for keyword in CATEGORIES:
                result = scrape_category(browser, keyword, args.pages)
                results[keyword] = result
                print(f"\n  ✅ '{keyword}': {result['count']} products")
                time.sleep(random.uniform(5, 9))
        finally:
            browser.close()

    # Save results
    total = sum(r["count"] for r in results.values())
    output_data = {
        "scraped_at": timestamp,
        "region": "Poland/English",
        "total_products": total,
        "results": results,
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
