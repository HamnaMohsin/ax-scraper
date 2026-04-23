"""
run_scraper_all_ids.py
──────────────────────
Reads aliexpress_products_filtered_by_category.json,
extracts every product ID across ALL categories,
then calls scrape_product_details_bulk() from scraper.py.

Usage:
    python run_scraper_all_ids.py
    python run_scraper_all_ids.py --input path/to/filtered.json --output results.json
"""

import argparse
import json
import sys
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
    help="Output file for scraped results",
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

# results is a dict keyed by category keyword
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

print(f"\n[INFO] Starting scrape → output: {args.output}\n")
print("─" * 55)

# ── Import & run scraper ──────────────────────────────────────────────────────
try:
    from scraper import scrape_product_details_bulk
except ImportError:
    sys.exit(
        "[ERROR] Cannot import scraper.py — make sure it's in the same directory.\n"
        "        Run:  python run_scraper_all_ids.py"
    )

summary = scrape_product_details_bulk(
    product_ids=all_ids,
    output_file=args.output,
)

# ── Final report ──────────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("SCRAPE COMPLETE")
print("=" * 55)
print(f"  Total scraped : {summary['total']}")
print(f"  Saved to      : {summary['saved_to']}")

results = summary.get("results", [])
ok      = [r for r in results if not r.get("errors")]
partial = [r for r in results if r.get("errors") and len(r["errors"]) < 3]
failed  = [r for r in results if len(r.get("errors", [])) == 3]

print(f"\n  ✅ Full data   : {len(ok)}")
print(f"  ⚠️  Partial     : {len(partial)}")
print(f"  ❌ All failed  : {len(failed)}")

if failed:
    print("\n  Failed IDs:")
    for r in failed:
        print(f"    • {r['id']}")
