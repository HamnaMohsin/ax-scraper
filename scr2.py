"""
AliExpress Product ID + Title Scraper v6
Key fix: rotates Tor AND rebuilds the browser context (fresh cookies/fingerprint)
on every CAPTCHA hit, instead of just sending NEWNYM once per category.

Other improvements over v5:
  - wait_for_selector() after page load to confirm JS products are rendered
  - networkidle fallback when domcontentloaded doesn't expose products
  - relaxed is_nested_anchor() (only skips if BOTH parent AND grandparent are <a>)
  - pre-flight IP check: rotates until AliExpress home loads clean before scraping
  - per-category context rebuild so blocked cookies don't persist
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
    "lapdesks",
    "led strip lights",
    "phone case",
    "laptop stand",
    "smart watch",
]
MAX_PAGES_PER_CATEGORY = 3
OUTPUT_FILE            = "aliexpress_products.json"
MAX_CAPTCHA_ROTATIONS  = 6    # per page attempt
ROTATE_WAIT_SECS       = 14   # seconds to wait after NEWNYM
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = (
    "https://www.aliexpress.com/w/wholesale-{slug}.html"
    "?SearchText={query}&catId=0&g=y&shipFromCountry=&trafficChannel=main&page={page}"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

CAPTCHA_URL_TOKENS = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
CAPTCHA_SELECTORS  = [
    "iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
    "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']",
]

# Selector that appears when product cards are rendered
PRODUCT_CARD_SELECTOR = "a[href*='/item/']"


# ── Helpers ───────────────────────────────────────────────────────────────────
def rotate_tor_circuit(wait: int = ROTATE_WAIT_SECS) -> bool:
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        time.sleep(wait)
        print(f"   ✅ Tor rotated (waited {wait}s)")
        return True
    except Exception as e:
        print(f"   ⚠️  Tor rotation failed: {e}")
        return False


def random_viewport() -> dict:
    return random.choice([
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
    ])


def is_captcha(page) -> bool:
    url = page.url.lower()
    if any(t in url for t in CAPTCHA_URL_TOKENS):
        return True
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


def make_context(browser):
    """Create a fresh browser context with randomised fingerprint."""
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random_viewport(),
        locale="en-US",
        timezone_id="Europe/Warsaw",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9,pl;q=0.8"},
    )
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda r: r.abort())
    return ctx


def make_page(context):
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en','pl']});
        window.chrome = {runtime: {}};
    """)
    return page


# ── Pre-flight: find a clean Tor IP ──────────────────────────────────────────
def preflight_clean_ip(browser, max_attempts: int = 15) -> bool:
    """
    Rotate Tor until AliExpress home loads without CAPTCHA.
    Returns True when a clean circuit is found.
    """
    print("\n  🔍 Pre-flight: finding clean Tor exit IP …")
    for attempt in range(1, max_attempts + 1):
        print(f"   [{attempt:02d}/{max_attempts}] Rotating …", end=" ", flush=True)
        rotate_tor_circuit(wait=12)

        ctx  = make_context(browser)
        page = make_page(ctx)
        clean = False
        try:
            page.goto(
                "https://www.aliexpress.com/?lang=en&shipToCountry=PL",
                wait_until="domcontentloaded",
                timeout=35000,
            )
            time.sleep(3)
            if not is_captcha(page):
                url = page.url.lower()
                if "aliexpress" in url and "punish" not in url:
                    clean = True
        except Exception:
            pass
        finally:
            ctx.close()

        if clean:
            print("✅ Clean!")
            print(f"  ✅ Pre-flight passed on attempt {attempt}")
            return True
        else:
            print("❌ Blocked")

    print(f"  ❌ Could not find clean IP in {max_attempts} attempts")
    return False


# ── Language / region ─────────────────────────────────────────────────────────
def set_poland_english(page) -> bool:
    """Navigate to the locale-setting URL. Verify Poland flag appears."""
    try:
        page.goto(
            "https://www.aliexpress.com/?lang=en&shipToCountry=PL&currency=PLN",
            wait_until="domcontentloaded",
            timeout=35000,
        )
        time.sleep(3)
        if is_captcha(page):
            return False
        # Inject locale cookie as belt-and-braces
        page.evaluate("""
            () => {
                document.cookie = 'aep_usuc_f=site=glo&c_tp=PLN&region=PL&b_locale=en_US;path=/;domain=.aliexpress.com';
                document.cookie = 'intl_locale=en_US;path=/;domain=.aliexpress.com';
            }
        """)
        return True
    except Exception as e:
        print(f"   ⚠️  Language set error: {e}")
        return True   # non-fatal, continue scraping


# ── Page loading with per-CAPTCHA context rebuild ─────────────────────────────
def load_page_with_rotation(browser, url: str) -> tuple:
    """
    Attempt to load *url*, rotating Tor and rebuilding the browser context
    on every CAPTCHA hit.

    Returns (page, context) on success, or (None, None) on total failure.
    The caller is responsible for closing context when done.
    """
    for attempt in range(MAX_CAPTCHA_ROTATIONS + 1):
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            print(f"   📡 Loading (attempt {attempt + 1}) …")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Wait for actual product cards to appear (handles JS render delay)
            try:
                page.wait_for_selector(PRODUCT_CARD_SELECTOR, timeout=12000)
            except PlaywrightTimeout:
                pass   # may still have products; we'll check below

            time.sleep(random.uniform(3, 5))

            if is_captcha(page):
                print(f"   ❌ CAPTCHA on attempt {attempt + 1} — rotating …")
                ctx.close()
                if attempt < MAX_CAPTCHA_ROTATIONS:
                    rotate_tor_circuit()
                continue

            # Quick sanity: at least some item links?
            item_count = page.locator(PRODUCT_CARD_SELECTOR).count()
            print(f"   📋 URL: {page.url[:80]}")
            print(f"   📋 /item/ links visible: {item_count}")

            if item_count > 0:
                return page, ctx   # success

            # Zero items but no CAPTCHA — try networkidle once
            if attempt == 0:
                print("   ⏳ No items yet, waiting for networkidle …")
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightTimeout:
                    pass
                time.sleep(3)
                item_count = page.locator(PRODUCT_CARD_SELECTOR).count()
                print(f"   📋 /item/ links after networkidle: {item_count}")
                if item_count > 0:
                    return page, ctx

            ctx.close()
            print("   ⚠️  No products — rotating circuit and retrying …")
            rotate_tor_circuit(wait=10)

        except Exception as exc:
            print(f"   ❌ Navigation error: {exc}")
            try:
                ctx.close()
            except Exception:
                pass
            if attempt < MAX_CAPTCHA_ROTATIONS:
                rotate_tor_circuit(wait=8)

    return None, None


# ── HTML parsing ──────────────────────────────────────────────────────────────
def clean_title(raw: str) -> str:
    return " ".join(raw.split()).strip()


def is_nested_anchor(tag) -> bool:
    """
    Returns True only when the <a> is nested inside ANOTHER <a>.
    (Relaxed vs v4/v5 which also checked grandparent — was too aggressive.)
    """
    parent = tag.parent
    if parent and parent.name == "a":
        return True
    return False


def extract_products_from_html(html: str) -> tuple[list[dict], dict]:
    soup     = BeautifulSoup(html, "html.parser")
    seen_ids: set[str] = set()
    products: list[dict] = []
    stats    = {"ssr_skipped": 0, "nested_skipped": 0, "tier": {}}
    id_re    = re.compile(r'/item/(\d{10,20})\.html')

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        if "/ssr/" in href:
            stats["ssr_skipped"] += 1
            continue

        m = id_re.search(href)
        if not m:
            continue
        product_id = m.group(1)
        if product_id in seen_ids:
            continue

        if is_nested_anchor(a_tag):
            stats["nested_skipped"] += 1
            continue

        seen_ids.add(product_id)
        title = ""
        tier  = "missing"

        h3 = a_tag.find("h3")
        if h3:
            title = clean_title(h3.get_text())
            tier  = "h3"

        if not title:
            heading = a_tag.find(attrs={"role": "heading"})
            if heading and heading.get("aria-label"):
                title = clean_title(heading["aria-label"])
                tier  = "aria-label"

        if not title and a_tag.get("title"):
            title = clean_title(a_tag["title"])
            tier  = "title-attr"

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


# ── Category scraper ──────────────────────────────────────────────────────────
def scrape_category(browser, keyword: str, max_pages: int) -> dict:
    print(f"\n{'━'*60}")
    print(f"  🔍  {keyword.upper()}")
    print(f"{'━'*60}")

    all_products: list[dict] = []
    seen_ids: set[str] = set()

    for page_num in range(1, max_pages + 1):
        slug  = keyword.strip().replace(" ", "-")
        query = keyword.strip().replace(" ", "+")
        url   = (
            f"https://www.aliexpress.com/w/wholesale-{slug}.html"
            f"?SearchText={query}&catId=0&g=y&shipFromCountry=&trafficChannel=main&page={page_num}"
        )
        print(f"\n  [Page {page_num}/{max_pages}]  {url}")

        page, ctx = load_page_with_rotation(browser, url)

        if page is None:
            print("   ❌ All rotation attempts exhausted — skipping page")
            continue

        try:
            # Scroll to trigger lazy loading
            for _ in range(5):
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                time.sleep(0.5)
            time.sleep(2)

            html = page.content()
            page_products, stats = extract_products_from_html(html)

            new_products = [p for p in page_products if p["id"] not in seen_ids]
            for p in new_products:
                seen_ids.add(p["id"])
            all_products.extend(new_products)

            print(f"  ✓ {len(new_products)} new | cumulative: {len(all_products)}")
            print(f"    tiers: {stats['tier']} | nested_skipped: {stats['nested_skipped']}")
            for p in new_products[:2]:
                title = (p["title"][:60] + "…") if len(p["title"]) > 60 else p["title"]
                print(f"    ↳ {p['id']} [{p['_tier']}] {title}")

            if len(new_products) == 0 and page_num > 1:
                print("  ⚠️  No new products — stopping category early")
                break

        finally:
            ctx.close()

        time.sleep(random.uniform(6, 11))

    clean = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {"keyword": keyword, "products": clean, "count": len(clean)}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AliExpress Scraper v6")
    parser.add_argument("--headless",     default="true", choices=["true", "false"])
    parser.add_argument("--pages",        type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output",       default=OUTPUT_FILE)
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip the clean-IP pre-flight check")
    args = parser.parse_args()

    headless  = args.headless.lower() == "true"
    timestamp = datetime.now().isoformat()

    print(f"\n{'═'*70}")
    print("  🚀 AliExpress Scraper v6 — Poland/English + Auto-Rotate")
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
            # Pre-flight: ensure we start with a clean IP
            if not args.skip_preflight:
                if not preflight_clean_ip(browser):
                    print("\n  ⚠️  Pre-flight failed — starting anyway (may hit CAPTCHAs)")

            for keyword in CATEGORIES:
                result = scrape_category(browser, keyword, args.pages)
                results[keyword] = result
                print(f"\n  ✅ '{keyword}': {result['count']} products")
                # Rotate between categories to get a fresh IP
                print("  🔄 Rotating circuit between categories …")
                rotate_tor_circuit(wait=15)

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
