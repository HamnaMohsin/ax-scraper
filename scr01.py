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
PRODUCT_IDS = [
    "1005010256644429",
]

OUTPUT_FILE           = "aliexpress_product_details.json"
MAX_CAPTCHA_ROTATIONS = 8
ROTATE_WAIT_SECS      = 14

PRODUCT_URL = "https://pl.aliexpress.com/item/{product_id}.html"

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

DELIVERY_FALLBACKS = [
    "strong[data-spm-anchor-id*='i5']",
    ".dynamic-shipping-line strong",
    "[class*='shipping'] strong",
]
QUANTITY_FALLBACKS = [
    "div.quantity--info--jnoo_pD span",
    "[class*='quantity--info'] span",
    "[class*='quantity'] span[data-spm-anchor-id*='i7']",
]
RATING_FALLBACKS = [
    "a.reviewer--rating--xrWWFzx > strong",
    "[class*='reviewer--rating'] strong",
    "[class*='rating'] strong",
]

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,  "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10,"nov": 11, "dec": 12,
}


# ── Logging ───────────────────────────────────────────────────────────────────
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
    ctx.add_cookies([
        {"name": "aep_usuc_f",    "value": "site=pol&c_tp=USD&region=PL&b_locale=en_US", "domain": ".aliexpress.com", "path": "/"},
        {"name": "xman_us_f",     "value": "x_locale=en_US&x_site=POL",                  "domain": ".aliexpress.com", "path": "/"},
        {"name": "ali_apache_id", "value": "PL",                                           "domain": ".aliexpress.com", "path": "/"},
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


# ── Parsing helpers ───────────────────────────────────────────────────────────
def parse_delivery_dates(raw: str) -> dict:
    raw  = raw.strip()
    now  = datetime.now()
    year = now.year

    # "Apr 26 - 29" — same month
    m = re.match(r'([A-Za-z]+)\s+(\d+)\s*[-–]\s*(\d+)', raw)
    if m:
        month = MONTH_MAP.get(m.group(1).lower()[:3])
        if month:
            y = year if month >= now.month else year + 1
            try:
                return {
                    "raw":  raw,
                    "from": datetime(y, month, int(m.group(2))).strftime("%Y-%m-%d"),
                    "to":   datetime(y, month, int(m.group(3))).strftime("%Y-%m-%d"),
                }
            except ValueError:
                pass

    # "Apr 30 - May 3" — cross-month
    m = re.match(r'([A-Za-z]+)\s+(\d+)\s*[-–]\s*([A-Za-z]+)\s+(\d+)', raw)
    if m:
        mo1 = MONTH_MAP.get(m.group(1).lower()[:3])
        mo2 = MONTH_MAP.get(m.group(3).lower()[:3])
        if mo1 and mo2:
            y1 = year if mo1 >= now.month else year + 1
            y2 = year if mo2 >= now.month else year + 1
            try:
                return {
                    "raw":  raw,
                    "from": datetime(y1, mo1, int(m.group(2))).strftime("%Y-%m-%d"),
                    "to":   datetime(y2, mo2, int(m.group(4))).strftime("%Y-%m-%d"),
                }
            except ValueError:
                pass

    return {"raw": raw, "from": None, "to": None}


def parse_quantity(raw: str) -> int | None:
    m = re.search(r'\d+', raw.replace(",", ""))
    return int(m.group()) if m else None


# ── Selector helper ───────────────────────────────────────────────────────────
def try_selectors(page, selectors: list[str], field_name: str, indent: int = 2) -> str | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                text = loc.inner_text().strip()
                if text:
                    log("✓", f"{field_name} found via '{sel}': {text!r}", indent=indent)
                    return text
        except Exception:
            continue
    log("⚠️ ", f"{field_name} not found with any selector", indent=indent)
    return None


# ── Page loader (needs an existing browser instance) ─────────────────────────
def load_product_page(browser, url: str):
    for attempt in range(MAX_CAPTCHA_ROTATIONS + 1):
        log("📡", f"Loading attempt {attempt + 1}/{MAX_CAPTCHA_ROTATIONS + 1} …", indent=1)
        ctx  = make_context(browser)
        page = make_page(ctx)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector(".pdp-info, .product-info, [class*='pdp-body']", timeout=15000)
            except PlaywrightTimeout:
                log("⚠️ ", "PDP container not found", indent=1)

            time.sleep(random.uniform(2, 4))

            if is_captcha(page):
                log("❌", "CAPTCHA detected — rotating …", indent=1)
                ctx.close()
                rotate_tor_circuit()
                continue

            for _ in range(4):
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.6)")
                time.sleep(0.4)
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)

            log("✅", "Page loaded successfully", indent=1)
            return page, ctx

        except Exception as exc:
            log("❌", f"Navigation error: {exc}", indent=1)
            ctx.close()
            rotate_tor_circuit()

    return None, None


# ── Per-product scraper (uses existing browser) ───────────────────────────────
def scrape_product(browser, product_id: str) -> dict:
    url = PRODUCT_URL.format(product_id=product_id)
    log("🛒", f"Scraping product ID: {product_id}")

    result = {
        "product_id":    product_id,
        "url":           url,
        "scraped_at":    datetime.now().isoformat(),
        "delivery_date": None,
        "quantity":      None,
        "rating":        None,
        "errors":        [],
    }

    page, ctx = load_product_page(browser, url)
    if page is None:
        result["errors"].append("Failed to load page after max retries")
        return result

    try:
        raw_delivery = try_selectors(page, DELIVERY_FALLBACKS, "Delivery date")
        if raw_delivery:
            result["delivery_date"] = parse_delivery_dates(raw_delivery)
        else:
            result["errors"].append("delivery_date: not found")

        raw_qty = try_selectors(page, QUANTITY_FALLBACKS, "Quantity")
        if raw_qty:
            result["quantity"] = {"raw": raw_qty, "value": parse_quantity(raw_qty)}
        else:
            result["errors"].append("quantity: not found")

        raw_rating = try_selectors(page, RATING_FALLBACKS, "Rating")
        if raw_rating:
            try:
                result["rating"] = float(raw_rating.strip())
            except ValueError:
                result["rating"] = raw_rating.strip()
        else:
            result["errors"].append("rating: not found")

    finally:
        ctx.close()

    log("✅", f"Done — delivery: {result['delivery_date']}, qty: {result['quantity']}, rating: {result['rating']}", indent=1)
    return result


# ── Self-contained scraper — NO browser argument needed (for FastAPI import) ──
def scrape_product_details(product_id: int) -> dict:
    """
    Standalone entry point used by main.py.
    Spins up its own Playwright + Tor browser, scrapes one product, closes.
    """
    url = PRODUCT_URL.format(product_id=str(product_id))
    result = {
        "product_id":    product_id,
        "url":           url,
        "scraped_at":    datetime.utcnow().isoformat(),
        "delivery_date": None,
        "quantity":      None,
        "rating":        None,
        "errors":        [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            page, ctx = load_product_page(browser, url)
            if page is None:
                result["errors"].append("Failed to load page after max retries")
                return result

            try:
                raw_delivery = try_selectors(page, DELIVERY_FALLBACKS, "Delivery date")
                if raw_delivery:
                    result["delivery_date"] = parse_delivery_dates(raw_delivery)
                else:
                    result["errors"].append("delivery_date: not found")

                raw_qty = try_selectors(page, QUANTITY_FALLBACKS, "Quantity")
                if raw_qty:
                    result["quantity"] = {"raw": raw_qty, "value": parse_quantity(raw_qty)}
                else:
                    result["errors"].append("quantity: not found")

                raw_rating = try_selectors(page, RATING_FALLBACKS, "Rating")
                if raw_rating:
                    try:
                        result["rating"] = float(raw_rating.strip())
                    except ValueError:
                        result["rating"] = raw_rating.strip()
                else:
                    result["errors"].append("rating: not found")

            finally:
                ctx.close()
        finally:
            browser.close()

    return result


# ── CLI entry point ───────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="AliExpress Product Detail Scraper")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--output",   default=OUTPUT_FILE)
    parser.add_argument("--ids",      nargs="*", default=None)
    args = parser.parse_args()

    product_ids = args.ids or PRODUCT_IDS

    log_separator("═")
    log("🚀", "AliExpress Product Detail Scraper")
    log("📋", f"Products to scrape: {len(product_ids)}")
    log_separator("═")

    all_results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless.lower() == "true",
            proxy={"server": "socks5://127.0.0.1:9050"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            for i, pid in enumerate(product_ids, 1):
                log_separator()
                log("🗂️ ", f"Product {i}/{len(product_ids)}: {pid}")
                result = scrape_product(browser, pid)
                all_results.append(result)
                if i < len(product_ids):
                    wait = random.uniform(5, 12)
                    log("⏳", f"Waiting {wait:.1f}s …", indent=1)
                    time.sleep(wait)
                    rotate_tor_circuit()
        finally:
            browser.close()

    output_path = Path(args.output)
    existing: dict = {"products": {}}
    if output_path.exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    if "products" not in existing:
        existing["products"] = {}

    for r in all_results:
        existing["products"][r["product_id"]] = r

    existing["last_updated"]   = datetime.now().isoformat()
    existing["total_products"] = len(existing["products"])

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    log_separator("═")
    log("🎉", f"COMPLETE. Saved to {args.output}")
    log("📊", f"Total products: {existing['total_products']}")
    log_separator("═")


if __name__ == "__main__":
    main()
