"""
AliExpress Product Scraper
- Scrapes: star rating + delivery date per product ID
- Runs through Tor proxy (VM-ready)
- Multiple selector strategies + HTML regex fallbacks
- Debug dumps HTML + screenshot on failure

Requirements:
    pip install playwright beautifulsoup4 stem
    playwright install chromium
    # Tor must be running:  sudo apt install tor && sudo service tor start
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
    from stem import Signal
    from stem.control import Controller
except ImportError:
    sys.exit("pip install stem")


# ── Config ────────────────────────────────────────────────────────────────────
PRODUCT_IDS = []  # Pass IDs via CLI args or the FastAPI bulk endpoint

BASE_URL   = "https://pl.aliexpress.com/item/{id}.html?language=en&currency=PLN"
OUTPUT_FILE = "ax_products.json"
DEBUG_DIR   = Path("debug")
DEBUG_FAILED = True

MAX_CAPTCHA_ROTATIONS         = 5   # CLI default
ROTATE_WAIT_SECS              = 14  # CLI default
MAX_CAPTCHA_ROTATIONS_API     = 8   # more retries when called from FastAPI
ROTATE_WAIT_SECS_API          = 25  # longer wait between rotations via API

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


# ── Selectors ─────────────────────────────────────────────────────────────────
RATING_SELECTORS = [
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-left > div.pdp-info > div.pdp-info-right > div.reviewer--wrap--vGS7G6P > div > div > a.reviewer--rating--xrWWFzx > strong",
    "a.reviewer--rating--xrWWFzx strong",
    "strong[data-spm-anchor-id]",
    "[class*='reviewer--wrap'] strong",
    "[class*='reviewer--rating'] strong",
    "[class*='reviewer'] strong",
]

RATING_HTML_PATTERNS = [
    r"(?:&nbsp;|\s){1,4}(\d\.\d)(?:&nbsp;|\s){1,4}",
    r'"reviewStar":\s*"?(\d\.\d)"?',
    r'"averageStar":\s*"?(\d\.\d)"?',
    r'"starRating":\s*"?(\d\.\d)"?',
    r'"rating":\s*"?(\d\.\d)"?',
    r'starScore["\s:]+(\d\.\d)',
]

# Selectors specifically for the date line (contentLayout), not the free-shipping title line
DELIVERY_SELECTORS = [
    # Original selector
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(5) > div:nth-child(1) > div > div > div.dynamic-shipping-line.dynamic-shipping-contentLayout > span:nth-child(1) > span > strong",
    # New variant: no nth-child(1) on the span
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(5) > div:nth-child(1) > div > div > div.dynamic-shipping-line.dynamic-shipping-contentLayout > span > span > strong",
    "div.dynamic-shipping-contentLayout strong",
    "[class*='dynamic-shipping-contentLayout'] strong",
]

# Month names in Polish and English for date validation
DATE_RE = re.compile(
    # Matches 'Apr 25', 'Friday Apr 22', 'Apr 25 - 29'
    r'(?:\d{1,2}.{0,6})?'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec'
    r'|Sty|Lut|Kwi|Maj|Cze|Lip|Sie|Wrz|Paz|Lis|Gru)'
    r'.{0,6}\d{1,2}',
    re.IGNORECASE
)

DELIVERY_HTML_PATTERNS = [
    r'(?:Get it before|Delivery:|odbierz przed|Dostawa:)[^<"]{2,60}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Sty|Lut|Kwi|Maj|Cze|Lip|Sie|Wrz|Paz|Lis|Gru)[^<"]{1,20}',
    r'"deliveryDayMax":\s*"([^"]+)"',
    r'"promiseDate":\s*"([^"]+)"',
]

PRICE_SELECTORS = [
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-left > div.pdp-info > div.pdp-info-right > div.price-default--wrap--uwQneeq > div > div.price-default--bannerContent--RVEikiQ > div.price-default--priceWrap--y4ppSfS > div.price-default--currentWrap--A_MNgCG > span",
    "[class*='price-default--current']",
    "[class*='price--currentPriceText']",
    "[class*='uniform-banner-box-price']",
]

PRICE_HTML_PATTERNS = [
    r'"discountPrice":\s*\{[^}]*"formattedPrice":\s*"([^"]+)"',
    r'"salePrice":\s*\{[^}]*"formattedPrice":\s*"([^"]+)"',
    r'"originalPrice":\s*\{[^}]*"formattedPrice":\s*"([^"]+)"',
]

QUANTITY_SELECTORS = [
    # Case 1: "294 available" — text is in a <span>
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(7) > div.quantity--info--jnoo_pD > div > span",
    # Case 2: "Max. 1 pcs/shopper" — text is in a <div>
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(7) > div.quantity--info--jnoo_pD > div > div",
    # Shorter fallbacks — specific enough to avoid grabbing wrapper divs
    "[class*='quantity--info'] > div > span",
    "[class*='quantity--info'] > div > div",
]

QUANTITY_HTML_PATTERNS = [
    r'"totalAvailQuantity":\s*(\d+)',
    r'"availQuantity":\s*(\d+)',
    r'Only\s+(\d+)\s+left',
    r'Max\.?\s*(\d+)\s*pcs',
]

SHIP_COUNTRY_SELECTORS = [
    # Full path — titleLayout span 4
    "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(5) > div:nth-child(1) > div > div > div.dynamic-shipping-line.dynamic-shipping-titleLayout > span:nth-child(4) > span",
    # Shorter fallbacks
    "div.dynamic-shipping-titleLayout span:nth-child(4) > span",
    "[class*='dynamic-shipping-titleLayout'] span:nth-child(4) > span",
    "[class*='dynamic-shipping-titleLayout'] span[data-spm-anchor-id]",
]

# Matches "Ship from France", "Ships from China", etc.
SHIP_COUNTRY_RE = re.compile(
    r'[Ss]hip(?:s|ped)?\s+from\s+([A-Za-z\u00C0-\u00FF\s]+)',
    re.IGNORECASE
)

SHIP_COUNTRY_HTML_PATTERNS = [
    # Matches the span content: 'Ship from France'
    r'[Ss]hip\s+from\s+([A-Za-z\u00C0-\u00FF][A-Za-z\u00C0-\u00FF ]{1,39}?)(?:<|"|&|\s*\})',
    r'"shipFrom":\s*"([^"]+)"',
    r'"sendGoodsCountry":\s*"([^"]+)"',
    r'"sourceCountry":\s*"([^"]+)"',
    # Sometimes embedded in JSON data blobs
    r'"shippingFrom":\s*"([^"]+)"',
    r'"originCountry":\s*"([^"]+)"',
]


# ── Logging ───────────────────────────────────────────────────────────────────
def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(emoji, msg, indent=0):
    print(f"[{ts()}] {'  ' * indent}{emoji}  {msg}", flush=True)


# ── Tor ───────────────────────────────────────────────────────────────────────
def rotate_tor_circuit(wait=ROTATE_WAIT_SECS):
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


# ── Browser context ───────────────────────────────────────────────────────────
def make_context(browser):
    ctx = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale="pl-PL",
        timezone_id="Europe/Warsaw",
        geolocation={"latitude": 52.2297, "longitude": 21.0122},
        permissions=["geolocation"],
        viewport={"width": 1280, "height": 800},
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
        },
    )
    ctx.add_cookies([
        {"name": "aep_usuc_f",  "value": "site=glo&c_tp=PLN&region=PL&b_locale=en_US", "domain": ".aliexpress.com", "path": "/"},
        {"name": "intl_locale", "value": "en_US",  "domain": ".aliexpress.com", "path": "/"},
        {"name": "aep_history", "value": "PL",     "domain": ".aliexpress.com", "path": "/"},
    ])
    # Block heavy assets
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf}", lambda r: r.abort())
    return ctx

def make_page(ctx):
    page = ctx.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}, app: {}};
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer'},
                {name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                {name: 'Native Client',      filename: 'internal-nacl-plugin'},
            ]
        });
        Object.defineProperty(navigator, 'languages', {get: () => ['pl-PL', 'pl', 'en-US', 'en']});
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : origQuery(p);
        Object.defineProperty(screen, 'width',       {get: () => 1920});
        Object.defineProperty(screen, 'height',      {get: () => 1080});
        Object.defineProperty(screen, 'availWidth',  {get: () => 1920});
        Object.defineProperty(screen, 'availHeight', {get: () => 1040});
        Object.defineProperty(screen, 'colorDepth',  {get: () => 24});
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
        Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});
        delete window.__playwright;
        delete window.__pw_manual;
        delete window.callPhantom;
        delete window._phantom;
    """)
    return page

def is_captcha(page):
    if any(t in page.url.lower() for t in CAPTCHA_URL_TOKENS):
        return True
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


# ── Scraping helpers ──────────────────────────────────────────────────────────
def scrape_selector(page, selectors):
    """Try each CSS selector, return first non-empty inner text."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                text = el.inner_text(timeout=3000).strip()
                if text:
                    return text, f"selector"
        except Exception:
            continue
    return None, "none"

def scrape_html_regex(page, patterns):
    """Try each regex against raw page HTML."""
    try:
        html = page.content()
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                value = m.group(1) if m.lastindex else m.group()
                value = re.sub(r"&nbsp;", " ", value).strip()
                return value, "html regex"
    except Exception:
        pass
    return None, "none"

def get_rating(page):
    # CSS selectors
    text, method = scrape_selector(page, RATING_SELECTORS)
    if text:
        m = re.search(r"\d\.\d", text)
        if m:
            return m.group(), method

    # All <strong> tags scan
    try:
        for el in page.locator("strong").all():
            try:
                text = el.inner_text(timeout=1000).strip()
                if re.fullmatch(r"\d\.\d", text):
                    return text, "strong scan"
            except Exception:
                continue
    except Exception:
        pass

    # HTML regex
    return scrape_html_regex(page, RATING_HTML_PATTERNS)

def clean_delivery(text):
    # Strip any language prefix like "Delivery:", "Dostawa:", etc.
    return re.sub(r"^[^:]+:\s*", "", text, flags=re.IGNORECASE).strip()

def has_date(text):
    """Return True only if the text contains a recognisable delivery date."""
    return bool(DATE_RE.search(text))

def get_delivery(page):
    # CSS selectors — only accept if text contains an actual date
    text, method = scrape_selector(page, DELIVERY_SELECTORS)
    if text and has_date(text):
        return clean_delivery(text), method

    # Fallback: scan ALL strong tags for one containing a date
    try:
        for el in page.locator("strong").all():
            try:
                t = el.inner_text(timeout=1000).strip()
                if has_date(t):
                    return clean_delivery(t), "strong scan"
            except Exception:
                continue
    except Exception:
        pass

    # HTML regex
    text, method = scrape_html_regex(page, DELIVERY_HTML_PATTERNS)
    if text:
        text = clean_delivery(text)
    return text, method

def get_price(page):
    # CSS selectors
    text, method = scrape_selector(page, PRICE_SELECTORS)
    if text:
        return text.strip(), method
    # HTML regex
    return scrape_html_regex(page, PRICE_HTML_PATTERNS)

def get_quantity(page):
    # Try both full CSS paths explicitly (span = available count, div = max pcs)
    SPAN_SEL = "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(7) > div.quantity--info--jnoo_pD > div > span"
    DIV_SEL  = "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-right > div > div > div:nth-child(7) > div.quantity--info--jnoo_pD > div > div"
    for sel in [SPAN_SEL, DIV_SEL]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                t = el.inner_text(timeout=3000).strip()
                if t:
                    return t, "selector"
        except Exception:
            continue

    # Shorter fallback selectors
    text, method = scrape_selector(page, QUANTITY_SELECTORS)
    if text:
        return text.strip(), method

    # HTML regex — returns a number string
    text, method = scrape_html_regex(page, QUANTITY_HTML_PATTERNS)
    if text:
        if text.isdigit():
            text = f"Only {text} left" if int(text) < 50 else f"{text} available"
    return text, method

def get_ship_country(page):
    # CSS selectors — extract country name from "Ship from X" text
    text, method = scrape_selector(page, SHIP_COUNTRY_SELECTORS)
    if text:
        m = SHIP_COUNTRY_RE.search(text)
        if m:
            return m.group(1).strip(), method
        # Selector returned raw country name (no "Ship from" prefix)
        if text and not any(c.isdigit() for c in text):
            return text.strip(), method

    # HTML regex
    text, method = scrape_html_regex(page, SHIP_COUNTRY_HTML_PATTERNS)
    if text:
        m = SHIP_COUNTRY_RE.search(text)
        return (m.group(1).strip() if m else text.strip()), method

    return None, "none"


# ── Debug dump ────────────────────────────────────────────────────────────────
def dump_debug(page, product_id):
    DEBUG_DIR.mkdir(exist_ok=True)
    html_path = DEBUG_DIR / f"{product_id}.html"
    shot_path = DEBUG_DIR / f"{product_id}.png"
    try:
        html = page.content()
        html_path.write_text(html, encoding="utf-8")
        page.screenshot(path=str(shot_path), full_page=False)
        log("🐛", f"Debug saved → {html_path} | {shot_path}", indent=1)

        # Show relevant classes
        for keyword in ["reviewer", "shipping", "delivery", "dynamic"]:
            classes = re.findall(rf'class="([^"]*{keyword}[^"]*)"', html, re.IGNORECASE)
            if classes:
                log("🔍", f"'{keyword}' classes:", indent=2)
                for c in sorted(set(classes))[:5]:
                    print(f"         {c}")
    except Exception as e:
        log("⚠️ ", f"Debug dump failed: {e}", indent=1)


# ── Per-product scraper ───────────────────────────────────────────────────────
def scrape_product(browser, product_id, max_retries=MAX_CAPTCHA_ROTATIONS, wait_secs=ROTATE_WAIT_SECS):
    url = BASE_URL.format(id=product_id)
    log("📦", f"Product ID: {product_id}")
    log("🔗", f"URL: {url}", indent=1)

    for attempt in range(max_retries + 1):
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            log("→", f"Loading (attempt {attempt + 1}) …", indent=1)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            # Wait for rating, delivery, or ship country element
            for sel in RATING_SELECTORS[:2] + DELIVERY_SELECTORS[:2] + SHIP_COUNTRY_SELECTORS[:2]:
                try:
                    page.wait_for_selector(sel, timeout=6_000)
                    break
                except PlaywrightTimeout:
                    pass

            # Human-like mouse movement + scroll to avoid bot detection
            try:
                page.mouse.move(random.randint(300, 900), random.randint(200, 600))
                time.sleep(random.uniform(0.3, 0.7))
                page.mouse.move(random.randint(100, 800), random.randint(100, 500))
                page.evaluate("window.scrollBy(0, {y})".format(y=random.randint(200, 500)))
                time.sleep(random.uniform(0.5, 1.0))
                page.evaluate("window.scrollBy(0, {y})".format(y=random.randint(100, 300)))
            except Exception:
                pass

            time.sleep(random.uniform(2, 4))

            if is_captcha(page):
                log("❌", "CAPTCHA — rotating Tor …", indent=1)
                ctx.close()
                rotate_tor_circuit(wait=wait_secs)
                continue

            rating,       r_method  = get_rating(page)
            delivery,     d_method  = get_delivery(page)
            price,        p_method  = get_price(page)
            quantity,     q_method  = get_quantity(page)
            ship_country, sc_method = get_ship_country(page)

            icon_r  = "✅" if rating       else "❌"
            icon_d  = "✅" if delivery     else "❌"
            icon_p  = "✅" if price        else "❌"
            icon_q  = "✅" if quantity     else "❌"
            icon_sc = "✅" if ship_country else "❌"
            log(icon_r,  f"Rating      : {rating       or 'not found'}  ({r_method})",  indent=1)
            log(icon_d,  f"Delivery    : {delivery     or 'not found'}  ({d_method})",  indent=1)
            log(icon_p,  f"Price       : {price        or 'not found'}  ({p_method})",  indent=1)
            log(icon_q,  f"Quantity    : {quantity     or 'not found'}  ({q_method})",  indent=1)
            log(icon_sc, f"Ship country: {ship_country or 'not found'}  ({sc_method})", indent=1)

            all_null = not any([rating, delivery, price, quantity, ship_country])
            if DEBUG_FAILED and not all([rating, delivery, price]):
                # Log what page actually loaded to diagnose blocks
                try:
                    actual_url   = page.url
                    actual_title = page.title()
                    log("🔍", f"Page URL  : {actual_url}", indent=1)
                    log("🔍", f"Page title: {actual_title}", indent=1)
                except Exception:
                    pass
                dump_debug(page, product_id)

            # If everything is null, treat as a soft failure and rotate for next product
            if all_null:
                log("⚠️ ", "All fields null — page likely blocked or empty", indent=1)

            return {"id": product_id, "rating": rating, "delivery": delivery,
                    "price": price, "quantity": quantity, "ship_country": ship_country}

        except Exception as e:
            log("✗", f"Error: {e}", indent=1)
            try:
                dump_debug(page, product_id)
            except Exception:
                pass
            rotate_tor_circuit(wait=wait_secs)
        finally:
            ctx.close()

    log("❌", f"Failed after {max_retries + 1} attempts", indent=1)
    return {"id": product_id, "rating": None, "delivery": None, "price": None, "quantity": None, "ship_country": None}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AliExpress Rating + Delivery Scraper")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--output",   default=OUTPUT_FILE)
    parser.add_argument("ids", nargs="*", default=PRODUCT_IDS,
                        help="Product IDs to scrape  e.g.  1005011748833056 1005011606028187")
    args = parser.parse_args()

    if not args.ids:
        parser.error("No product IDs provided. Pass them as positional arguments:\n"
                     "  python scraper.py 1005011748833056 1005011606028187")

    headless = args.headless.lower() == "true"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy={"server": "socks5://127.0.0.1:9050"},   # Tor SOCKS5 proxy
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-ipc-flooding-protection",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-background-timer-throttling",
                "--window-size=1920,1080",
            ],
        )

        results = []
        try:
            for i, product_id in enumerate(args.ids):
                result = scrape_product(browser, product_id)
                results.append(result)
                if i < len(args.ids) - 1:
                    time.sleep(random.uniform(3, 6))
        finally:
            browser.close()

    # ── Save ──────────────────────────────────────────────────────────────────
    output_path = Path(args.output)

    output_data = {
        "last_updated": datetime.now().isoformat(),
        "total": len(results),
        "results": results,
    }
    output_path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("RESULTS SUMMARY")
    print("=" * 55)
    print(f"  {'ID':<25} {'Rating':<8} {'Price':<14} {'Quantity':<16} {'Ship From':<16} {'Delivery'}")
    print(f"  {'-'*24} {'-'*7} {'-'*13} {'-'*15} {'-'*15} {'-'*30}")
    for r in results:
        rating       = r.get("rating")       or "N/A"
        delivery     = r.get("delivery")     or "N/A"
        price        = r.get("price")        or "N/A"
        quantity     = r.get("quantity")     or "N/A"
        ship_country = r.get("ship_country") or "N/A"
        print(f"  {r['id']:<25} {rating:<8} {price:<14} {quantity:<16} {ship_country:<16} {delivery}")
    print(f"\n  Saved → {args.output}")


if __name__ == "__main__":
    main()

# ── Public API (importable by FastAPI / main.py) ──────────────────────────────

def scrape_product_details(product_id: int | str) -> dict:
    """
    Scrape a single product and return a structured dict.
    Called by FastAPI endpoints in main.py.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-ipc-flooding-protection",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-background-timer-throttling",
                "--window-size=1920,1080",
            ],
        )
        try:
            result = scrape_product(browser, str(product_id))
        finally:
            browser.close()

    errors = [f for f in ["rating", "delivery", "price"] if not result.get(f)]
    return {
        "url":          BASE_URL.format(id=product_id),
        "scraped_at":   datetime.now().isoformat(),
        "rating":       result.get("rating"),
        "delivery":     result.get("delivery"),
        "price":        result.get("price"),
        "quantity":     result.get("quantity"),
        "ship_country": result.get("ship_country"),
        "errors":       errors,
    }


def scrape_product_details_bulk(
    product_ids: list,
    output_file: str = OUTPUT_FILE,
) -> dict:
    """
    Scrape a list of product IDs, save to JSON, return summary dict.
    Always fetches live data — no caching.
    Called by FastAPI bulk endpoints in main.py.
    """
    to_scrape = [str(pid) for pid in product_ids]
    for pid in to_scrape:
        log("🔄", f"ID {pid} — scraping live data")

    fresh_results = []
    if to_scrape:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-ipc-flooding-protection",
                    "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-background-timer-throttling",
                    "--window-size=1920,1080",
                ],
            )
            try:
                for i, pid in enumerate(to_scrape):
                    r = scrape_product(
                        browser, str(pid),
                        max_retries=MAX_CAPTCHA_ROTATIONS_API,
                        wait_secs=ROTATE_WAIT_SECS_API,
                    )

                    r["scraped_at"] = datetime.now().isoformat()
                    r["url"]        = BASE_URL.format(id=pid)
                    r["errors"]     = [f for f in ["rating", "delivery", "price"] if not r.get(f)]

                    # If all fields null, rotate Tor before next product
                    if not any([r.get("rating"), r.get("delivery"), r.get("price")]):
                        log("⚠️ ", f"ID {pid} — all null, rotating Tor before next product …")
                        rotate_tor_circuit(wait=ROTATE_WAIT_SECS_API)

                    fresh_results.append(r)
                    if i < len(to_scrape) - 1:
                        time.sleep(random.uniform(5, 10))
            finally:
                browser.close()

    # Save fresh results — no merging with old data
    output_path = Path(output_file)
    output_data = {
        "last_updated": datetime.now().isoformat(),
        "total":        len(fresh_results),
        "results":      fresh_results,
    }
    output_path.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "saved_to": str(output_path.resolve()),
        "total":    len(fresh_results),
        "results":  fresh_results,
    }
