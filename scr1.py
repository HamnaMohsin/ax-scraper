"""
AliExpress Product ID + Title Scraper
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Searches N categories on AliExpress, paginates through results,
and collects { product_id, title } pairs.

Fix v2: skips nested <a> mini-cards ("Similar items" / m5_* cards)
        that had empty alt text and no <h3>, causing ~5-12 missing
        titles per category at the end of the extracted list.

SSR/deal URLs (aliexpress.com/ssr/...) are also skipped for now.

v3: Added Tor circuit rotation + CAPTCHA detection with retries.

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

def random_viewport():
    return random.choice([
        {"width": 1920, "height": 1080},
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864}
    ])

def diagnose_page(page, keyword: str, page_num: int):
    """Diagnose why no products are found"""
    print(f"   📋 URL: {page.url}")
    print(f"   📋 Title: {page.title()[:80]}...")
    
    if is_captcha_page(page):
        print("   ❌ CAPTCHA/BLOCK DETECTED")
        return False
    
    html = page.content()
    with open(f"debug_{keyword}_p{page_num}.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"   💾 HTML saved: debug_{keyword}_p{page_num}.html")
    
    item_links = len(page.locator("a[href*='/item/']").all())
    products = len(page.locator("[class*='product'], [class*='item']").all())
    print(f"   📋 Item links: {item_links} | Product cards: {products}")
    
    return item_links > 5  # Need at least 5 item links

def is_captcha_page(page) -> bool:
    title = page.title().lower()
    url = page.url.lower()
    
    captcha_words = ["captcha", "verify", "baxia", "punish", "blocked", "access denied", "no results"]
    if any(word in title or word in url for word in captcha_words):
        return True
        
    return page.locator("iframe[src*='recaptcha'], .baxia-punish, [class*='captcha']").count() > 0

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

    Why this matters:
      AliExpress main product cards (class="search-card-item") each contain
      a small "Similar items" / "You may also like" row of mini-cards
      (class="m5_*") that are additional <a href="/item/..."> tags nested
      INSIDE the outer card's <a> tag.

      These mini-cards have:
        • No <h3>
        • No aria-label / role="heading"
        • Empty alt="" on their <img> tags

      ...so all 4 title-extraction tiers fail, producing "—" titles.
      The product IDs *are* valid, but the items belong to other,
      unrelated searches.  The cleanest fix is to skip them entirely:
      they will appear as top-level cards in their own right elsewhere
      in the page (or in a later page), where they DO carry full markup.
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

    products  — list of {"id": "...", "title": "...", "_tier": "..."}
    stats     — diagnostic counts (ssr_skipped, nested_skipped, tier_breakdown)

    Card structure observed in the wild
    ────────────────────────────────────
    Outer (main) card:
        <a class="search-card-item" href="/item/<id>.html">
            <div role="heading" aria-label="<title>">
                <h3 class="lw_k4"><title></h3>
            </div>
            <!-- nested mini-cards (lw_ly / m5_* wrapper) -->
            <div class="lw_ly">
                <a href="/item/<other-id>.html">   ← nested, NO title markup
                    <img alt="">
                </a>
            </div>
        </a>

    Title extraction — 4-tier fallback (most → least reliable):
        1. <h3> anywhere inside the <a>
        2. aria-label on a child element with role="heading"
        3. title attribute on the <a> itself
        4. alt text of the first non-empty <img> inside the card
    """
    soup = BeautifulSoup(html, "html.parser")
    seen_ids: set[str] = set()
    products: list[dict] = []
    stats = {"ssr_skipped": 0, "nested_skipped": 0, "tier": {}}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        # ── Skip SSR / promotional URLs ───────────────────────────────────────
        if is_ssr_url(href):
            stats["ssr_skipped"] += 1
            continue

        # ── Must be a standard /item/<id>.html link ───────────────────────────
        product_id = extract_product_id_from_href(href)
        if not product_id or product_id in seen_ids:
            continue

        # ── KEY FIX: skip mini-cards nested inside another <a> ────────────────
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
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
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
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        window.chrome = {runtime: {}};
    """)

    try:
        for page_num in range(1, max_pages + 1):
            url = build_url(keyword, page_num)
            print(f"\n  [Page {page_num}/{max_pages}]  {url}")

            # Navigate with diagnostics
            success = False
            for attempt in range(2):  # 2 attempts per page
                try:
                    print(f"   📡 Loading (attempt {attempt+1})...")
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    time.sleep(4)  # Let JS render

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
    print("  AliExpress Product Scraper  (ID + Title)  v3")
    print(f"  Started   : {timestamp}")
    print(f"  Headless  : {headless}  |  Pages/category: {max_pages}")
    print(f"  Categories: {', '.join(CATEGORIES)}")
    print(f"{'═'*60}")

    results: dict[str, dict] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
    headless=headless,
    proxy={"server": "socks5://127.0.0.1:9050"},  # ← ADD TOR PROXY
    args=[
        "--no-sandbox", 
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage"  # ← ADD FOR GCP
    ],
)
        try:
            for keyword in CATEGORIES:
                result = scrape_category(browser, keyword, max_pages)
                results[keyword] = result
                print(f"\n  ▶  '{keyword}': {result['count']} products collected.")
                human_delay(4, 8)
        finally:
            browser.close()

    total = sum(r["count"] for r in results.values())
    output_data = {
        "scraped_at":          timestamp,
        "categories_searched": len(CATEGORIES),
        "pages_per_category":  max_pages,
        "total_products":      total,
        "note":                "SSR/deal URLs and nested mini-cards are skipped in this version.",
        "results":             results,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n{'═'*60}")
    print("  SCRAPING COMPLETE")
    print(f"{'═'*60}")
    print(f"  Total products : {total}")
    print(f"  Output file    : {output.resolve()}")
    print()
    for kw, r in results.items():
        print(f"  {kw:<22} → {r['count']:>4} products")
        for p in r["products"][:2]:
            title_preview = p["title"][:65] + ("…" if len(p["title"]) > 65 else "")
            print(f"    • {p['id']}  |  {title_preview}")
    print()


if __name__ == "__main__":
    main()
