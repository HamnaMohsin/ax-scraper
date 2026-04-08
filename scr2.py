"""
AliExpress Scraper v7 — English title enforcement
Root cause fix: Tor exit IPs in DE/AT/CH cause AliExpress to serve German
titles regardless of ?lang=en in the URL. Fix: inject locale cookies on
EVERY new context AND add an Accept-Language header that strongly prefers
English over German.
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
MAX_CAPTCHA_ROTATIONS  = 6
ROTATE_WAIT_SECS       = 14
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = (
    "https://www.aliexpress.com/w/wholesale-{slug}.html"
    "?SearchText={query}&catId=0&g=y&shipFromCountry="
    "&trafficChannel=main&page={page}&language=en&lang=en"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── The key fix: Accept-Language that gives German q=0.1 (near-zero weight) ──
# AliExpress reads this header to pick the UI language when no valid
# locale cookie is set yet.
ENGLISH_ACCEPT_LANG = "en-US,en;q=0.9,pl;q=0.5,de;q=0.1"

# ── The locale cookie AliExpress actually respects ────────────────────────────
# aep_usuc_f encodes: site, currency, ship-to country, and UI locale.
# b_locale=en_US is the field that controls product title language.
LOCALE_COOKIE = "aep_usuc_f=site=glo&c_tp=PLN&region=PL&b_locale=en_US"
INTL_COOKIE   = "intl_locale=en_US"
XMAN_COOKIE   = "xman_us_f=x_locale=en_US&x_l=1&x_c=PL"   # extra locale signal

CAPTCHA_URL_TOKENS = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
CAPTCHA_SELECTORS  = [
    "iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
    "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']",
]
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
        {"width": 1440, "height": 900},  {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},  {"width": 1920, "height": 1080},
    ])


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


def make_context(browser):
    """
    Fresh context with:
      • Accept-Language strongly preferring English over German
      • Locale cookies pre-added at the storage-state level
    """
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random_viewport(),
        locale="en-US",
        timezone_id="Europe/Warsaw",
        extra_http_headers={
            # The single most effective fix — demote de to q=0.1
            "Accept-Language": ENGLISH_ACCEPT_LANG,
        },
    )
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda r: r.abort())

    # Pre-seed locale cookies for the aliexpress.com domain
    # so they exist before the very first request
    ctx.add_cookies([
        {"name": "aep_usuc_f", "value": "site=glo&c_tp=PLN&region=PL&b_locale=en_US",
         "domain": ".aliexpress.com", "path": "/"},
        {"name": "intl_locale", "value": "en_US",
         "domain": ".aliexpress.com", "path": "/"},
        {"name": "xman_us_f",   "value": "x_locale=en_US&x_l=1&x_c=PL",
         "domain": ".aliexpress.com", "path": "/"},
        # aep_currency — tells AliExpress we want PLN/PL pricing
        {"name": "aep_currency", "value": "PLN",
         "domain": ".aliexpress.com", "path": "/"},
    ])
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


def reinject_locale_cookies(page):
    """
    Re-inject the locale cookies via JS after page load.
    Belt-and-braces: some AliExpress responses overwrite aep_usuc_f
    with a geo-detected locale — we overwrite it back.
    """
    page.evaluate(f"""
        () => {{
            const set = (n, v) => {{
                document.cookie = n + '=' + v + ';path=/;domain=.aliexpress.com;max-age=86400';
            }};
            set('aep_usuc_f',  'site=glo&c_tp=PLN&region=PL&b_locale=en_US');
            set('intl_locale', 'en_US');
            set('xman_us_f',   'x_locale=en_US&x_l=1&x_c=PL');
        }}
    """)


def detect_page_language(page) -> str:
    """
    Heuristic: sample the first visible product title text and guess language.
    Returns 'en', 'de', or 'unknown'.
    """
    try:
        # Grab text from the first few item links
        sample = []
        for a in page.locator(PRODUCT_CARD_SELECTOR).all()[:5]:
            try:
                txt = a.inner_text(timeout=1000).strip()
                if txt:
                    sample.append(txt)
            except Exception:
                pass

        combined = " ".join(sample).lower()
        # German dead giveaways
        de_signals = ["hülle", "für", "schutz", "zubehör", "stoßfest",
                      "handyhülle", "abdeckung", "bildschirm", "uhren",
                      "smartwatch für", "männer", "frauen"]
        de_hits = sum(1 for s in de_signals if s in combined)
        if de_hits >= 2:
            return "de"
        return "en" if combined else "unknown"
    except Exception:
        return "unknown"


# ── Pre-flight: find clean IP ─────────────────────────────────────────────────
def preflight_clean_ip(browser, max_attempts: int = 15) -> bool:
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
                wait_until="domcontentloaded", timeout=35000,
            )
            time.sleep(3)
            if not is_captcha(page):
                if "aliexpress" in page.url.lower() and "punish" not in page.url.lower():
                    clean = True
        except Exception:
            pass
        finally:
            ctx.close()

        if clean:
            print("✅ Clean!")
            return True
        print("❌ Blocked")

    print(f"  ❌ Could not find clean IP in {max_attempts} attempts")
    return False


# ── Page loading with rotation ────────────────────────────────────────────────
def load_page_with_rotation(browser, url: str):
    """
    Load url, rotating Tor + rebuilding context on each CAPTCHA.
    After a successful load, re-injects locale cookies and checks
    the page language — rotates again if German is detected.

    Returns (page, context) or (None, None).
    """
    for attempt in range(MAX_CAPTCHA_ROTATIONS + 1):
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            print(f"   📡 Loading (attempt {attempt + 1}) …")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            try:
                page.wait_for_selector(PRODUCT_CARD_SELECTOR, timeout=12000)
            except PlaywrightTimeout:
                pass

            time.sleep(random.uniform(3, 5))

            if is_captcha(page):
                print(f"   ❌ CAPTCHA — rotating …")
                ctx.close()
                if attempt < MAX_CAPTCHA_ROTATIONS:
                    rotate_tor_circuit()
                continue

            item_count = page.locator(PRODUCT_CARD_SELECTOR).count()
            if item_count == 0:
                # Try networkidle once
                if attempt == 0:
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except PlaywrightTimeout:
                        pass
                    time.sleep(3)
                    item_count = page.locator(PRODUCT_CARD_SELECTOR).count()

                if item_count == 0:
                    ctx.close()
                    rotate_tor_circuit(wait=10)
                    continue

            # ── Language check ─────────────────────────────────────────────
            # Re-inject cookies first, then check what language we got
            reinject_locale_cookies(page)
            time.sleep(1)

            lang = detect_page_language(page)
            print(f"   📋 {item_count} items | detected language: {lang}")

            if lang == "de":
                print("   🇩🇪 German titles detected — exit node is in DE/AT/CH")
                print("   🔄 Rotating to find English-serving exit …")
                ctx.close()
                rotate_tor_circuit(wait=14)
                continue

            # All good
            return page, ctx

        except Exception as exc:
            print(f"   ❌ Error: {exc}")
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

        h3 = a_tag.find("h3")
        if h3:
            title, tier = clean_title(h3.get_text()), "h3"

        if not title:
            heading = a_tag.find(attrs={"role": "heading"})
            if heading and heading.get("aria-label"):
                title, tier = clean_title(heading["aria-label"]), "aria-label"

        if not title and a_tag.get("title"):
            title, tier = clean_title(a_tag["title"]), "title-attr"

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
    print(f"\n{'━'*60}\n  🔍  {keyword.upper()}\n{'━'*60}")

    all_products: list[dict] = []
    seen_ids: set[str] = set()

    for page_num in range(1, max_pages + 1):
        slug  = keyword.strip().replace(" ", "-")
        query = keyword.strip().replace(" ", "+")
        url   = BASE_URL.format(slug=slug, query=query, page=page_num)
        print(f"\n  [Page {page_num}/{max_pages}]  {url}")

        page, ctx = load_page_with_rotation(browser, url)
        if page is None:
            print("   ❌ All rotation attempts exhausted — skipping page")
            continue

        try:
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
                t = (p["title"][:60] + "…") if len(p["title"]) > 60 else p["title"]
                print(f"    ↳ {p['id']} [{p['_tier']}] {t}")

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
    parser = argparse.ArgumentParser(description="AliExpress Scraper v7")
    parser.add_argument("--headless",       default="true", choices=["true","false"])
    parser.add_argument("--pages",          type=int, default=MAX_PAGES_PER_CATEGORY)
    parser.add_argument("--output",         default=OUTPUT_FILE)
    parser.add_argument("--categories",     nargs="*", default=None)
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()

    categories = args.categories or CATEGORIES
    headless   = args.headless.lower() == "true"
    timestamp  = datetime.now().isoformat()

    print(f"\n{'═'*70}")
    print("  🚀 AliExpress Scraper v7 — English enforcement + Auto-Rotate")
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
            if not args.skip_preflight:
                if not preflight_clean_ip(browser):
                    print("\n  ⚠️  Pre-flight failed — starting anyway")

            for keyword in categories:
                result = scrape_category(browser, keyword, args.pages)
                results[keyword] = result
                print(f"\n  ✅ '{keyword}': {result['count']} products")
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
    print(f"  🎉 DONE — {total:,} products → {args.output}")
    print(f"{'═'*70}")


if __name__ == "__main__":
    main()
