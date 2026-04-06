"""
AliExpress Product ID + Title Scraper  —  v3  (Tor edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Searches N categories on AliExpress, paginates through results,
and collects { product_id, title } pairs.

What's new in v3:
  • All traffic routed through Tor  (socks5://127.0.0.1:9050)
  • Tor circuit rotation via stem   (port 9051)
  • CAPTCHA / block-page detection  with auto IP-rotate + retry
  • Exit-node pre-flight check      (httpbin.org/ip before touching AliExpress)
  • Random viewport, user-agent, timezone per browser context
  • webdriver fingerprint suppressed via add_init_script

Requirements:
    pip install playwright beautifulsoup4 stem
    playwright install chromium

    # Tor must be running on the GCP VM:
    #   SOCKS proxy  → 127.0.0.1:9050
    #   Control port → 127.0.0.1:9051  (with HashedControlPassword or CookieAuth)

Usage:
    python aliexpress_scraper_tor.py
    python aliexpress_scraper_tor.py --headless false
    python aliexpress_scraper_tor.py --pages 5
    python aliexpress_scraper_tor.py --output results.json
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
    "wireless earbuds",
    "led strip lights",
    "phone case",
    "laptop stand",
    "smart watch",
]

MAX_PAGES_PER_CATEGORY = 3
OUTPUT_FILE            = "aliexpress_products.json"
MAX_RETRIES            = 3          # attempts per category page before giving up

TOR_SOCKS_PORT         = 9050
TOR_CONTROL_PORT       = 9051
TOR_CONTROL_PASSWORD   = ""         # set if you used HashedControlPassword in torrc
                                    # leave empty to use cookie auth
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL = (
    "https://www.aliexpress.com/w/wholesale-{slug}.html"
    "?SearchText={query}&catId=0&g=y&shipFromCountry=&trafficChannel=main&page={page}"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

TIMEZONES = [
    "Europe/London", "Europe/Berlin", "Europe/Paris",
    "Europe/Amsterdam", "Europe/Warsaw", "America/New_York",
]

CARD_SELECTORS = [
    "a[href*='/item/']",
    "[class*='product-card']",
    "[class*='item-card']",
    "[class*='ProductCard']",
    "[class*='manhattan--container']",
]


# ── Tor helpers ───────────────────────────────────────────────────────────────

def rotate_tor_circuit() -> bool:
    """Send NEWNYM signal to get a fresh Tor exit node."""
    try:
        auth = TOR_CONTROL_PASSWORD if TOR_CONTROL_PASSWORD else None
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            if auth:
                controller.authenticate(password=auth)
            else:
                controller.authenticate()          # cookie auth fallback
            controller.signal(Signal.NEWNYM)
            print("   ⏳ Waiting 15 s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"      ... {14 - i} s remaining")
        print("✅ Tor circuit rotated — new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️  Could not rotate Tor circuit: {e}")
        return False


def check_exit_node(page) -> bool:
    """Hit httpbin before touching AliExpress — confirms the Tor circuit is alive."""
    try:
        page.goto("https://httpbin.org/ip", timeout=20_000, wait_until="domcontentloaded")
        body = page.inner_text("body").strip()
        print(f"   ✅ Exit node reachable: {body}")
        return True
    except Exception as e:
        print(f"   ⚠️  Exit node unreachable: {e}")
        return False


# ── CAPTCHA / block detection ─────────────────────────────────────────────────

def is_captcha_page(page) -> bool:
    page_url   = page.url.lower()
    page_title = page.title().lower()

    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    for sel in [
        "iframe[src*='recaptcha']",
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha']",
        "iframe[src*='geetest']",
        "[class*='captcha']",
    ]:
        try:
            if page.locator(sel).count() > 0:
                print(f"❌ CAPTCHA detected via selector: {sel}")
                return True
        except Exception:
            continue

    # Block / access-denied page heuristic
    is_product = "aliexpress" in page_title and len(page_title) > 40
    if not is_product and any(kw in page_title for kw in
                               ["verify", "access", "denied", "blocked", "challenge"]):
        print("❌ Block page detected from title")
        return True

    return False


# ── Browser factory ───────────────────────────────────────────────────────────

def new_browser_context(playwright):
    """
    Launch a fresh Chromium instance routed through Tor with a randomised
    fingerprint, and return (browser, context, page).
    """
    browser = playwright.chromium.launch(
        headless=True,
        proxy={"server": f"socks5://127.0.0.1:{TOR_SOCKS_PORT}"},
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )

    context = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random.choice(VIEWPORTS),
        locale="en-US",
        timezone_id=random.choice(TIMEZONES),
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    # Block images / fonts / tracking to speed things up
    context.route(
        "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,eot}",
        lambda r: r.abort(),
    )
    context.route(
        "**/{analytics,tracking,gtm,ga,pixel,beacon}**",
        lambda r: r.abort(),
    )

    page = context.new_page()

    # Suppress webdriver fingerprint
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page.add_init_script("Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3]})")

    return browser, context, page


# ── URL / tag helpers ─────────────────────────────────────────────────────────

def build_url(keyword: str, page_num: int) -> str:
    slug  = keyword.strip().replace(" ", "-")
    query = keyword.strip().replace(" ", "+")
    return BASE_URL.format(slug=slug, query=query, page=page_num)


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

        # Tier 1 — <h3>
        h3 = a_tag.find("h3")
        if h3:
            title = clean_title(h3.get_text())
            tier  = "h3"

        # Tier 2 — aria-label on role="heading"
        if not title:
            heading = a_tag.find(attrs={"role": "heading"})
            if heading and heading.get("aria-label"):
                title = clean_title(heading["aria-label"])
                tier  = "aria-label"

        # Tier 3 — title attribute on <a>
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

def scrape_page_with_retry(playwright, keyword: str, page_num: int,
                           seen_ids: set, max_pages: int) -> tuple[list[dict], bool]:
    """
    Scrape a single search-results page with up to MAX_RETRIES attempts.
    On CAPTCHA / timeout, rotates the Tor circuit and retries.

    Returns (new_products, reached_last_page).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n  [Page {page_num}/{max_pages}]  attempt {attempt}/{MAX_RETRIES}")

        if attempt > 1:
            rotate_tor_circuit()
            wait = 20 + attempt * 5
            print(f"   Waiting {wait} s before retry...")
            time.sleep(wait)

        browser, context, page = new_browser_context(playwright)

        try:
            # ── Exit-node pre-flight ──────────────────────────────────────────
            print("   🌐 Checking exit node...")
            if not check_exit_node(page):
                print("   ⚠️  Exit node down — will rotate and retry")
                browser.close()
                continue

            # ── Load the search page ──────────────────────────────────────────
            url = build_url(keyword, page_num)
            print(f"   📡 Loading: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            except PlaywrightTimeout:
                print("   ⚠️  Navigation timed out")
                browser.close()
                continue
            except Exception as exc:
                print(f"   ⚠️  Navigation error: {exc}")
                browser.close()
                continue

            # ── CAPTCHA check ─────────────────────────────────────────────────
            if is_captcha_page(page):
                print("   ⚠️  CAPTCHA — rotating IP...")
                browser.close()
                continue

            # ── Wait for product cards ────────────────────────────────────────
            detected = False
            for sel in CARD_SELECTORS:
                try:
                    page.wait_for_selector(sel, timeout=12_000)
                    detected = True
                    break
                except PlaywrightTimeout:
                    continue

            if not detected:
                print("   ⚠️  No product cards detected — possible CAPTCHA or last page.")

            # ── Lazy-load trigger ─────────────────────────────────────────────
            slow_scroll(page)
            human_delay(1.5, 3.0)

            # ── Post-scroll CAPTCHA check ─────────────────────────────────────
            if is_captcha_page(page):
                print("   ⚠️  CAPTCHA after scroll — rotating IP...")
                browser.close()
                continue

            # ── Extract products ──────────────────────────────────────────────
            html = page.content()
            page_products, stats = extract_products_from_html(html)

            new_products = [p for p in page_products if p["id"] not in seen_ids]
            for p in new_products:
                seen_ids.add(p["id"])

            print(
                f"   ✓ {len(page_products)} parsed  |  "
                f"{len(new_products)} new  |  "
                f"SSR skipped: {stats['ssr_skipped']}  |  "
                f"nested skipped: {stats['nested_skipped']}"
            )
            print(f"     Title tiers → {stats['tier']}")
            for p in new_products[:3]:
                preview = p["title"][:70] + ("…" if len(p["title"]) > 70 else "")
                print(f"    ↳  {p['id']}  [{p['_tier']}]  {preview}")

            # ── Check if this is the last page ────────────────────────────────
            reached_last: bool = page.evaluate("""
                () => {
                    const btn = document.querySelector(
                        '.comet-pagination-next, [aria-label="Next page"], [aria-label="Next"]'
                    );
                    if (!btn) return true;
                    return btn.disabled
                        || btn.classList.contains('disabled')
                        || btn.getAttribute('aria-disabled') === 'true';
                }
            """)

            browser.close()
            return new_products, (reached_last or not page_products)

        except Exception as exc:
            print(f"   ❌ Unexpected error on attempt {attempt}: {exc}")
            try:
                browser.close()
            except Exception:
                pass

    print(f"   ❌ Page {page_num} failed after {MAX_RETRIES} attempts — skipping.")
    return [], False


def scrape_category(playwright, keyword: str, max_pages: int) -> dict:
    print(f"\n{'━'*60}")
    print(f"  🔍  {keyword.upper()}")
    print(f"{'━'*60}")

    all_products: list[dict] = []
    seen_ids: set[str] = set()

    for page_num in range(1, max_pages + 1):
        new_products, is_last = scrape_page_with_retry(
            playwright, keyword, page_num, seen_ids, max_pages
        )
        all_products.extend(new_products)
        print(f"   ▶  Running total: {len(all_products)} products")

        if is_last:
            print("   ✓ Last page reached — stopping pagination.")
            break

        human_delay(2.5, 5.5)

    clean_products = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {
        "keyword":  keyword,
        "products": clean_products,
        "count":    len(clean_products),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AliExpress Scraper v3 — Tor edition")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--pages",  type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    max_pages = args.pages
    output    = Path(args.output)
    timestamp = datetime.now().isoformat()

    print(f"\n{'═'*60}")
    print("  AliExpress Product Scraper  (Tor edition)  v3")
    print(f"  Started   : {timestamp}")
    print(f"  Pages/cat : {max_pages}  |  Max retries/page: {MAX_RETRIES}")
    print(f"  Tor SOCKS : 127.0.0.1:{TOR_SOCKS_PORT}")
    print(f"  Tor ctrl  : 127.0.0.1:{TOR_CONTROL_PORT}")
    print(f"  Categories: {', '.join(CATEGORIES)}")
    print(f"{'═'*60}")

    results: dict[str, dict] = {}

    with sync_playwright() as pw:
        for keyword in CATEGORIES:
            result = scrape_category(pw, keyword, max_pages)
            results[keyword] = result
            print(f"\n  ▶  '{keyword}': {result['count']} products collected.")
            human_delay(4, 8)

    total = sum(r["count"] for r in results.values())
    output_data = {
        "scraped_at":          timestamp,
        "categories_searched": len(CATEGORIES),
        "pages_per_category":  max_pages,
        "total_products":      total,
        "note":                "v3: Tor proxy, circuit rotation, CAPTCHA detection + retry.",
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
            preview = p["title"][:65] + ("…" if len(p["title"]) > 65 else "")
            print(f"    • {p['id']}  |  {preview}")
    print()


if __name__ == "__main__":
    main()
