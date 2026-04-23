"""
run_scraper_all_ids.py
──────────────────────
Reads aliexpress_products_filtered_by_category.json,
extracts every product ID across ALL categories,
then scrapes each one — saving to file immediately after each result
so no data is lost if the run is interrupted.

Usage:
    python run_scraper_all_ids.py
    python run_scraper_all_ids.py --input path/to/filtered.json --output results.json
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path


# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument(
    "--input",
    default="aliexpress_products_filtered_by_category.json",
    help="Path to the filtered products JSON file",
)
parser.add_argument(
    "--output",
    default="ax_products_all.json",
    help="Output file for scraped results (written after every product)",
)
args = parser.parse_args()


# ── Load & extract IDs ────────────────────────────────────────────────────────
input_path = Path(args.input)
if not input_path.exists():
    sys.exit(f"[ERROR] File not found: {input_path}")

with open(input_path, encoding="utf-8") as f:
    data = json.load(f)

all_ids = []
seen    = set()

results_block = data.get("results", {})

for keyword, category_data in results_block.items():
    products = category_data.get("products", [])
    for product in products:
        pid = str(product.get("id", "")).strip()
        if pid and pid not in seen:
            all_ids.append(pid)
            seen.add(pid)

print(f"[INFO] Found {len(all_ids)} unique product IDs across {len(results_block)} categories:")
for kw in results_block:
    count = len(results_block[kw].get("products", []))
    print(f"         • {kw!r:30s}  ({count} products)")


# ── Load existing output (resume support) ─────────────────────────────────────
output_path = Path(args.output)
existing_map: dict = {}  # id -> result dict

if output_path.exists():
    try:
        existing_data = json.loads(output_path.read_text(encoding="utf-8"))
        for r in existing_data.get("results", []):
            if isinstance(r, dict) and r.get("id"):
                existing_map[str(r["id"])] = r
        print(f"[INFO] Resuming — {len(existing_map)} products already in output file, skipping them.")
    except Exception as e:
        print(f"[WARN] Could not read existing output ({e}), starting fresh.")

# Filter out already-scraped IDs
to_scrape = [pid for pid in all_ids if pid not in existing_map]
print(f"[INFO] {len(to_scrape)} products left to scrape.\n")
print("─" * 55)


# ── Save helper — writes full merged state after every product ────────────────
def save_to_file(result_map: dict, path: Path):
    output_data = {
        "last_updated": datetime.now().isoformat(),
        "total":        len(result_map),
        "results":      list(result_map.values()),
    }
    path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Import scraper ────────────────────────────────────────────────────────────
try:
    from scr04 import scrape_product, rotate_tor_circuit, BASE_URL, \
        MAX_CAPTCHA_ROTATIONS_API, ROTATE_WAIT_SECS_API
except ImportError:
    sys.exit(
        "[ERROR] Cannot import scr04.py — make sure it's in the same directory."
    )

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")


# ── Scrape loop — save after every product ────────────────────────────────────
print(f"\n[INFO] Starting scrape → output: {args.output}  (saved after every product)\n")

counters = {"ok": 0, "partial": 0, "failed": 0}

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
            for i, pid in enumerate(to_scrape, start=1):
                print(f"\n[{i}/{len(to_scrape)}] Scraping ID: {pid}")

                r = scrape_product(
                    browser, pid,
                    max_retries=MAX_CAPTCHA_ROTATIONS_API,
                    wait_secs=ROTATE_WAIT_SECS_API,
                )
                r["scraped_at"] = datetime.now().isoformat()
                r["url"]        = BASE_URL.format(id=pid)
                r["errors"]     = [f for f in ["rating", "delivery", "price"] if not r.get(f)]

                # Classify result
                err_count = len(r["errors"])
                if err_count == 0:
                    status = "✅ full"
                    counters["ok"] += 1
                elif err_count < 3:
                    status = "⚠️  partial"
                    counters["partial"] += 1
                else:
                    status = "❌ failed"
                    counters["failed"] += 1

                print(f"     → {status}  |  errors: {r['errors'] or 'none'}")

                # Merge into map and save immediately
                existing_map[pid] = r
                save_to_file(existing_map, output_path)
                print(f"     → 💾 Saved ({len(existing_map)} total in file)")

                # Rotate Tor if all fields null
                if err_count == 3:
                    print(f"     → All null — rotating Tor before next product …")
                    rotate_tor_circuit(wait=ROTATE_WAIT_SECS_API)

                # Inter-product delay (skip after last)
                if i < len(to_scrape):
                    delay = random.uniform(5, 10)
                    print(f"     → Waiting {delay:.1f}s …")
                    time.sleep(delay)

        except KeyboardInterrupt:
            print("\n\n[WARN] Interrupted by user — progress saved up to last completed product.")
        finally:
            browser.close()


# ── Final report ──────────────────────────────────────────────────────────────
skipped = len(all_ids) - len(to_scrape)

print("\n" + "=" * 55)
print("SCRAPE COMPLETE")
print("=" * 55)
print(f"  Total in file  : {len(existing_map)}")
print(f"  Scraped now    : {len(to_scrape)}")
print(f"  Skipped (done) : {skipped}")
print(f"\n  ✅ Full data   : {counters['ok']}")
print(f"  ⚠️  Partial     : {counters['partial']}")
print(f"  ❌ All failed  : {counters['failed']}")
print(f"\n  Saved to       : {output_path.resolve()}")

failed_ids = [pid for pid, r in existing_map.items() if len(r.get("errors", [])) == 3]
if failed_ids:
    print(f"\n  Failed IDs ({len(failed_ids)}):")
    for fid in failed_ids:
        print(f"    • {fid}")
