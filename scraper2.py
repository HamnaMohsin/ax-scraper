"""
STEP 1 — Extract product title from AliExpress
Nothing else. Just get the title reliably first.
"""

import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ── Selectors to try, in order of reliability ─────────────────────────────────
TITLE_SELECTORS = [
    '[data-pl="product-title"]',       # most specific, AliExpress standard
    'h1[class*="title"]',              # h1 with title in class name
    'h1',                              # any h1 on the page
    '[class*="product-title"]',        # class contains product-title
    '[class*="ProductTitle"]',         # camelCase variant
]


def extract_title(page) -> str:
    """
    Try each selector in order.
    Return the first non-empty title found (min 10 chars to skip junk).
    """
    for selector in TITLE_SELECTORS:
        try:
            elem = page.locator(selector).first
            if elem.count() == 0:
                print(f"   ✗ not found:  {selector}")
                continue

            text = elem.inner_text(timeout=3000).strip()

            if text and len(text) >= 10:
                print(f"   ✓ found via:  {selector}")
                print(f"   ✓ title:      {text[:100]}{'...' if len(text) > 100 else ''}")
                return text
            else:
                print(f"   ✗ too short:  {selector}  ({len(text)} chars)")

        except Exception as e:
            print(f"   ✗ error [{selector}]: {e}")

    return ""


def scrape_title(url: str) -> dict:
    print(f"\n{'─'*60}")
    print(f"  URL: {url}")
    print(f"{'─'*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        # Hide webdriver fingerprint
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        try:
            # ── 1. Navigate ───────────────────────────────────────────────────
            print("\n[1] Loading page...")
            page.goto(url, timeout=60000, wait_until="domcontentloaded")

            final_url = page.url
            if final_url != url:
                print(f"    Redirected → {final_url}")

            # ── 2. Wait for full JS render ────────────────────────────────────
            # networkidle = no network requests for 500ms → page is fully hydrated
            print("[2] Waiting for network idle (JS render)...")
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
                print("    ✓ network idle reached")
            except PlaywrightTimeoutError:
                # Non-fatal: AliExpress keeps polling, so networkidle may time out.
                # The important content is almost certainly loaded anyway.
                print("    ⚠ network idle timed out — continuing with what we have")

            # Small extra buffer for late-rendering React/Vue components
            time.sleep(2)

            # ── 3. Extract title ──────────────────────────────────────────────
            print("[3] Extracting title...")
            title = extract_title(page)

            # ── 4. Result ─────────────────────────────────────────────────────
            if title:
                print(f"\n✅  SUCCESS")
            else:
                print(f"\n❌  FAILED — no title found")
                # Save a screenshot so you can inspect the page state
                page.screenshot(path="/tmp/step1_debug.png")
                print("    Screenshot saved → /tmp/step1_debug.png")

            browser.close()

            return {
                "url": final_url,
                "title": title,
                "success": bool(title),
            }

        except Exception as e:
            print(f"\n❌  EXCEPTION: {e}")
            try:
                page.screenshot(path="/tmp/step1_error.png")
                print("    Screenshot saved → /tmp/step1_error.png")
            except:
                pass
            browser.close()
            return {"url": url, "title": "", "success": False}


# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    TEST_URL = "https://www.aliexpress.com/item/1005004557250962.html"
    result = scrape_title(TEST_URL)

    print("\n── RESULT ──")
    print(f"  success : {result['success']}")
    print(f"  title   : {result['title'][:120] if result['title'] else '(empty)'}")
