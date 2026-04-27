"""
AliExpress Store Item Count Scraper
Usage:
  python aliexpress_store_scraper.py 911431006
  python aliexpress_store_scraper.py 911431006 --output result.json
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# Tor is only needed if captcha/block appears
try:
    from stem import Signal
    from stem.control import Controller
    TOR_AVAILABLE = True
except ImportError:
    TOR_AVAILABLE = False


STORE_URL = "https://www.aliexpress.com/store/{store_id}/pages/all-items.html?shop_sortType=bestmatch_sort&gatewayAdapt=glo2swe"
MAX_RETRIES = 3


def rotate_tor():
    if not TOR_AVAILABLE:
        print("[WARN] stem not installed, cannot rotate Tor IP")
        return
    try:
        with Controller.from_port(port=9051) as ctrl:
            ctrl.authenticate()
            ctrl.signal(Signal.NEWNYM)
        print("[INFO] Tor IP rotated, waiting 10s...")
        time.sleep(10)
    except Exception as e:
        print(f"[WARN] Tor rotation failed: {e}")


def scrape(store_id: str) -> int | None:
    url = STORE_URL.format(store_id=store_id)
    proxy = {"server": "socks5://127.0.0.1:9050"} if TOR_AVAILABLE else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            proxy=proxy,
            args=["--no-sandbox"],
        )
        page = browser.new_page(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[INFO] Attempt {attempt} — {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Wait for JS to render the page content
            time.sleep(5)

            # Dismiss baxia dialog if present
            dialog = page.locator(".baxia-dialog")
            if dialog.count() > 0:
                style = (dialog.first.get_attribute("style") or "").lower()
                if "display: none" not in style:
                    print("[INFO] Baxia dialog detected, dismissing...")
                    close_btn = page.locator(".baxia-dialog-close")
                    if close_btn.count() > 0:
                        close_btn.first.click()
                        time.sleep(2)

            # Check for hard block / captcha — rotate Tor and retry
            if any(p in page.url for p in ["punish", "baxia.aliexpress"]):
                print("[WARN] Blocked — rotating Tor IP...")
                rotate_tor()
                continue

            # Extract item count from the span: "285 items"
            count_el = page.locator("#right > div > div:nth-child(2) > span")
            if count_el.count() > 0:
                text = count_el.first.inner_text().strip()
                match = re.search(r"([\d,]+)\s+items?", text, re.IGNORECASE)
                if match:
                    count = int(match.group(1).replace(",", ""))
                    print(f"[OK] Found: '{text}' → {count}")
                    browser.close()
                    return count

            print("[WARN] Item count not found this attempt")

        browser.close()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("store_id", help="AliExpress store ID")
    parser.add_argument("--output", default=None, help="Save result to JSON file")
    args = parser.parse_args()

    count = scrape(args.store_id)

    result = {
        "store_id":   args.store_id,
        "item_count": count,
        "scraped_at": datetime.now().isoformat(),
    }

    if count is not None:
        print(f"\n✅ Store {args.store_id} has {count} items")
    else:
        print("\n❌ Failed to extract item count")

    print(json.dumps(result, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Saved to {args.output}")

    return 0 if count is not None else 1


if __name__ == "__main__":
    sys.exit(main())
