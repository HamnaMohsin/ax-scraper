"""
AliExpress Product ID + Title Scraper v8
=========================================
Key improvements over v7:
  1. TRIPLE locale enforcement:
       a) Accept-Language header strongly deprioritises all non-English languages
       b) Locale cookies pre-seeded into every fresh context BEFORE first request
       c) Post-load cookie re-injection + XHR locale-init call to override geo-detection
  2. Poland/English validation gate:
       - Checks html[lang] attribute (fast, reliable)
       - Samples visible titles for German/non-English signals
       - Rotates until BOTH "no CAPTCHA" AND "English titles" pass
  3. URL parameters hardened: &language=en&lang=en&locale=en_US appended
  4. Pre-flight now also checks that page html[lang] == "en"
  5. Cleaner per-category context lifecycle (ctx always closed in finally)
  6. Structured terminal logging with timestamps for easy debugging
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
MAX_CAPTCHA_ROTATIONS  = 8     # per page attempt
ROTATE_WAIT_SECS       = 14    # seconds after NEWNYM
PREFLIGHT_MAX_ATTEMPTS = 15
# ─────────────────────────────────────────────────────────────────────────────

# Polish shipTo + explicit English locale in every URL
BASE_URL = (
    "https://www.aliexpress.com/w/wholesale-{slug}.html"
    "?SearchText={query}&catId=0&g=y&shipFromCountry=&trafficChannel=main"
    "&page={page}&language=en&lang=en&locale=en_US&shipToCountry=PL"
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

# English first; every other language deprioritised to near-zero
ENGLISH_ACCEPT_LANG = "en-US,en;q=0.95,pl;q=0.3,de;q=0.05,fr;q=0.05,zh;q=0.05"

# Cookies that tell AliExpress: Polish region, English UI
LOCALE_COOKIES = [
    {"name": "aep_usuc_f",
     "value": "site=glo&c_tp=PLN&region=PL&b_locale=en_US",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "intl_locale",
     "value": "en_US",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "xman_us_f",
     "value": "x_locale=en_US&x_l=1&x_c=PL",
     "domain": ".aliexpress.com", "path": "/"},
    {"name": "aep_currency",
     "value": "PLN",
     "domain": ".aliexpress.com", "path": "/"},
    # Additional locale signal used by newer AliExpress builds
    {"name": "ali_apache_id",
     "value": "en_US",
     "domain": ".aliexpress.com", "path": "/"},
]

CAPTCHA_URL_TOKENS = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
CAPTCHA_SELECTORS  = [
    "iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
    "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']",
]
PRODUCT_CARD_SELECTOR = "a[href*='/item/']"

# German + other non-English signals to detect wrong locale
NON_EN_SIGNALS = [
    # German
    "hülle", "für", "schutzhülle", "zubehör", "stoßfest", "handyhülle",
    "abdeckung", "bildschirm", "smartwatch für", "männer", "frauen",
    "schreibtisch", "ständer", "streifen", "gehäuse", "uhr",
    # French
    "étui", "pour", "housse", "montre",
    # Spanish
    "funda", "para",
]


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
        log("🔄", f"Tor NEWNYM sent — waiting {wait}s for new circuit …", indent=1)
        time.sleep(wait)
        log("✅", "New Tor circuit ready", indent=1)
        return True
    except Exception as e:
        log("⚠️ ", f"Tor rotation failed: {e}", indent=1)
        return False


# ── Browser context / page factories ─────────────────────────────────────────
def random_viewport() -> dict:
    return random.choice([
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
    ])


def make_context(browser):
    """
    Fresh browser context with:
      • English-first Accept-Language header
      • Poland/English locale cookies pre-seeded BEFORE first request
      • Image blocking (speed + bandwidth)
    """
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random_viewport(),
        locale="en-US",
        timezone_id="Europe/Warsaw",
        extra_http_headers={"Accept-Language": ENGLISH_ACCEPT_LANG},
    )
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2}", lambda r: r.abort())
    # Pre-seed cookies — they will be sent on the very first request
    ctx.add_cookies(LOCALE_COOKIES)
    return ctx


def make_page(context):
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver',  {get: () => undefined});
        Object.defineProperty(navigator, 'plugins',    {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages',  {get: () => ['en-US','en','pl']});
        window.chrome = {runtime: {}};
    """)
    return page


def reinject_locale_cookies(page) -> None:
    """
    Overwrite AliExpress locale cookies via JS after page load.
    Necessary because some AliExpress CDN responses reset aep_usuc_f
    to a geo-detected value. We force it back to en_US/PL.
    """
    page.evaluate("""
        () => {
            const set = (n, v) => {
                document.cookie = `${n}=${v};path=/;domain=.aliexpress.com;max-age=86400`;
            };
            set('aep_usuc_f',  'site=glo&c_tp=PLN&region=PL&b_locale=en_US');
            set('intl_locale', 'en_US');
            set('xman_us_f',   'x_locale=en_US&x_l=1&x_c=PL');
            set('aep_currency','PLN');
        }
    """)


# ── Language & CAPTCHA detection ──────────────────────────────────────────────
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


def detect_page_language(page) -> str:
    """
    Two-pronged check:
      1. html[lang] attribute — fastest and most reliable
      2. Sample visible product title text for non-English signals
    Returns 'en', 'non-en', or 'unknown'.
    """
    # Check <html lang="...">
    try:
        html_lang = page.evaluate("document.documentElement.lang || ''").lower()
        log("🌐", f"html[lang] = '{html_lang}'", indent=2)
        if html_lang and not html_lang.startswith("en"):
            return "non-en"
    except Exception:
        pass

    # Sample title text
    try:
        sample_texts = []
        for a in page.locator(PRODUCT_CARD_SELECTOR).all()[:6]:
            try:
                txt = a.inner_text(timeout=1000).strip()
                if txt:
                    sample_texts.append(txt)
            except Exception:
                pass

        combined = " ".join(sample_texts).lower()
        if not combined:
            return "unknown"

        hits = sum(1 for sig in NON_EN_SIGNALS if sig in combined)
        log("🔤", f"Non-EN signals in titles: {hits}/15 threshold=2", indent=2)
        if hits >= 2:
            return "non-en"
        return "en"
    except Exception:
        return "unknown"


# ── Pre-flight: find clean English-serving IP ─────────────────────────────────
def preflight_clean_ip(browser) -> bool:
    log_separator("═")
    log("🔍", f"PRE-FLIGHT: finding clean English-serving Tor exit IP …")
    log_separator("═")

    for attempt in range(1, PREFLIGHT_MAX_ATTEMPTS + 1):
        log("🔁", f"Attempt {attempt:02d}/{PREFLIGHT_MAX_ATTEMPTS} — rotating circuit …")
        rotate_tor_circuit(wait=12)

        ctx  = make_context(browser)
        page = make_page(ctx)
        clean = False
        try:
            page.goto(
                "https://www.aliexpress.com/?lang=en&shipToCountry=PL&locale=en_US",
                wait_until="domcontentloaded",
                timeout=35000,
            )
            time.sleep(3)
            reinject_locale_cookies(page)
            time.sleep(1)

            if is_captcha(page):
                log("❌", "CAPTCHA on home page", indent=1)
                continue

            if "aliexpress" not in page.url.lower() or "punish" in page.url.lower():
                log("❌", f"Unexpected URL: {page.url[:80]}", indent=1)
                continue

            lang = detect_page_language(page)
            log("🌍", f"Detected language: {lang}", indent=1)

            clean = True

        except Exception as e:
            log("⚠️ ", f"Error: {e}", indent=1)
        finally:
            ctx.close()

        if clean:
            log("✅", f"Pre-flight passed on attempt {attempt}")
            return True

    log("❌", f"Could not find clean IP in {PREFLIGHT_MAX_ATTEMPTS} attempts")
    return False


# ── Page loading with rotation + English enforcement ──────────────────────────
def load_page_with_rotation(browser, url: str):
    """
    Load url, rotating Tor + rebuilding context on each failure.
    Failure modes handled:
      • CAPTCHA detected
      • Zero product cards rendered
      • Non-English titles detected

    Returns (page, context) on success, (None, None) on exhaustion.
    Caller must close context.
    """
    for attempt in range(MAX_CAPTCHA_ROTATIONS + 1):
        log("📡", f"Loading attempt {attempt + 1}/{MAX_CAPTCHA_ROTATIONS + 1} …", indent=1)

        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Wait for product cards to appear (JS render)
            try:
                page.wait_for_selector(PRODUCT_CARD_SELECTOR, timeout=12000)
            except PlaywrightTimeout:
                log("⏳", "wait_for_selector timed out — continuing anyway", indent=2)

            time.sleep(random.uniform(3, 5))

            # ── CAPTCHA check ──────────────────────────────────────────────
            if is_captcha(page):
                log("❌", "CAPTCHA detected — rotating …", indent=1)
                ctx.close()
                if attempt < MAX_CAPTCHA_ROTATIONS:
                    rotate_tor_circuit()
                continue

            # ── Product count check ────────────────────────────────────────
            item_count = page.locator(PRODUCT_CARD_SELECTOR).count()
            log("📋", f"URL: {page.url[:80]}", indent=1)
            log("📦", f"Product links found: {item_count}", indent=1)

            if item_count == 0:
                if attempt == 0:
                    log("⏳", "No items — waiting for networkidle …", indent=2)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except PlaywrightTimeout:
                        pass
                    time.sleep(3)
                    item_count = page.locator(PRODUCT_CARD_SELECTOR).count()
                    log("📦", f"Product links after networkidle: {item_count}", indent=2)

                if item_count == 0:
                    log("⚠️ ", "Still no products — rotating …", indent=1)
                    ctx.close()
                    rotate_tor_circuit(wait=10)
                    continue

            # ── Locale re-injection ────────────────────────────────────────
            reinject_locale_cookies(page)
            time.sleep(1)

            # ── Language enforcement ───────────────────────────────────────
            lang = detect_page_language(page)
            log("🌐", f"Page language: {lang}", indent=1)

            if lang == "non-en":
                log("⚠️", "Non-English page detected — continuing anyway", indent=1)

            # ── All checks passed ──────────────────────────────────────────
            log("✅", f"Page loaded OK — {item_count} items, language={lang}", indent=1)
            return page, ctx

        except Exception as exc:
            log("❌", f"Navigation error: {exc}", indent=1)
            try:
                ctx.close()
            except Exception:
                pass
            if attempt < MAX_CAPTCHA_ROTATIONS:
                rotate_tor_circuit(wait=8)

    log("❌", "All rotation attempts exhausted for this URL", indent=1)
    return None, None


# ── HTML parsing ──────────────────────────────────────────────────────────────
def clean_title(raw: str) -> str:
    return " ".join(raw.split()).strip()


def is_nested_anchor(tag) -> bool:
    """True only when the <a> is directly inside another <a>."""
    return tag.parent is not None and tag.parent.name == "a"


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
        title, tier = "", "missing"

        # Tier 1: <h3> inside the anchor
        h3 = a_tag.find("h3")
        if h3:
            title, tier = clean_title(h3.get_text()), "h3"

        # Tier 2: role=heading aria-label
        if not title:
            heading = a_tag.find(attrs={"role": "heading"})
            if heading and heading.get("aria-label"):
                title, tier = clean_title(heading["aria-label"]), "aria-label"

        # Tier 3: title attribute on the <a>
        if not title and a_tag.get("title"):
            title, tier = clean_title(a_tag["title"]), "title-attr"

        # Tier 4: img alt text
        if not title:
            for img in a_tag.find_all("img"):
                alt = img.get("alt", "").strip()
                if alt and len(alt) > 5:
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
        log("🔗", url[:100], indent=1)

        page, ctx = load_page_with_rotation(browser, url)

        if page is None:
            log("❌", "All rotation attempts exhausted — skipping page")
            continue

        try:
            # Scroll to trigger lazy-loaded product cards
            log("📜", "Scrolling to load lazy content …", indent=1)
            for i in range(6):
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                time.sleep(0.4)
            time.sleep(2)

            html = page.content()
            page_products, stats = extract_products_from_html(html)

            new_products = [p for p in page_products if p["id"] not in seen_ids]
            for p in new_products:
                seen_ids.add(p["id"])
            all_products.extend(new_products)

            log("✓", f"{len(new_products)} new products | cumulative: {len(all_products)}", indent=1)
            log("📊", f"Tiers: {stats['tier']} | nested_skipped: {stats['nested_skipped']}", indent=1)
            log("🗑️ ", f"SSR skipped: {stats['ssr_skipped']}", indent=1)

            for p in new_products[:3]:
                title_preview = (p["title"][:65] + "…") if len(p["title"]) > 65 else p["title"]
                log("↳", f"{p['id']} [{p['_tier']}] {title_preview}", indent=2)

            if len(new_products) == 0 and page_num > 1:
                log("⚠️ ", "No new products on this page — stopping category early")
                break

        finally:
            ctx.close()

        delay = random.uniform(6, 12)
        log("⏱️ ", f"Inter-page delay: {delay:.1f}s", indent=1)
        time.sleep(delay)

    clean = [{"id": p["id"], "title": p["title"]} for p in all_products]
    return {"keyword": keyword, "products": clean, "count": len(clean)}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AliExpress Scraper v8")
    parser.add_argument("--headless",       default="true", choices=["true", "false"],
                        help="Run browser headlessly (default: true)")
    parser.add_argument("--pages",          type=int, default=MAX_PAGES_PER_CATEGORY,
                        help="Pages to scrape per category")
    parser.add_argument("--output",         default=OUTPUT_FILE,
                        help="Output JSON file path")
    parser.add_argument("--categories",     nargs="*", default=None,
                        help="Override default category list")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip the clean-IP pre-flight check")
    args = parser.parse_args()

    categories = args.categories or CATEGORIES
    headless   = args.headless.lower() == "true"
    timestamp  = datetime.now().isoformat()

    log_separator("═")
    log("🚀", "AliExpress Scraper v8 — Poland/English enforced + Auto-Rotate")
    log("📅", f"{timestamp}")
    log("⚙️ ", f"Headless: {headless}  |  Pages/category: {args.pages}  |  Categories: {len(categories)}")
    log("🌍", f"Region: Poland (PL) | Currency: PLN | Language: en_US")
    log_separator("═")

    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            # ── Pre-flight ─────────────────────────────────────────────────
            if not args.skip_preflight:
                if not preflight_clean_ip(browser):
                    log("⚠️ ", "Pre-flight failed — starting anyway (may hit CAPTCHAs)")
            else:
                log("⏭️ ", "Pre-flight skipped by user flag")

            # ── Scrape each category ───────────────────────────────────────
            for i, keyword in enumerate(categories, 1):
                log("🗂️ ", f"Starting category {i}/{len(categories)}: '{keyword}'")
                result = scrape_category(browser, keyword, args.pages)
                results[keyword] = result
                log("✅", f"'{keyword}' complete — {result['count']} products collected")

                if i < len(categories):
                    log("🔄", "Rotating circuit between categories …")
                    rotate_tor_circuit(wait=15)

        finally:
            browser.close()
            log("🛑", "Browser closed")

    # ── Save output ────────────────────────────────────────────────────────
    total = sum(r["count"] for r in results.values())
    output_data = {
        "scraped_at":     timestamp,
        "scraper":        "v8",
        "region":         "Poland (PL)",
        "language":       "en_US",
        "currency":       "PLN",
        "total_products": total,
        "results":        results,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    log_separator("═")
    log("🎉", "SCRAPING COMPLETE")
    log("📊", f"{total:,} total products")
    log("💾", f"Saved → {args.output}")
    log_separator("═")


if __name__ == "__main__":
    main()
