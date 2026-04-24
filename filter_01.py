"""
filter_products.py
──────────────────
Filters ax_products_all.json by:
  1. Delivery gap ≤ 16 days (scraped_at → later delivery date)
  2. Rating ≥ 4.0
  3. Converts PLN price to EUR (int, rounded)

Usage:
    python filter_products.py
    python filter_products.py --input ax_products_all.json --output filtered.json
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
PLN_TO_EUR      = 1 / 4.25   # 1 EUR ≈ 4.25 PLN  (update as needed)
MAX_DAYS        = 16
MIN_RATING      = 4.0

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--input",  default="ax_products_all.json")
parser.add_argument("--output", default="ax_products_filtered.json")
args = parser.parse_args()

# ── Month map ─────────────────────────────────────────────────────────────────
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def parse_later_delivery_date(delivery: str, scrape_year: int) -> datetime | None:
    """
    Returns the LATER (max) delivery date from a delivery string.

    Handles formats:
      "Apr. 29 - May. 03"
      "May 04 - 07"
      "Get it before Wednesday, Apr 25 - 29"
    """
    if not delivery:
        return None

    delivery = delivery.strip()

    # ── Format A: "Month Day - Month Day"  e.g. "Apr. 29 - May. 03"
    m = re.search(
        r'([A-Za-z]+)\.?\s+(\d{1,2})\s*-\s*([A-Za-z]+)\.?\s+(\d{1,2})',
        delivery
    )
    if m:
        month2 = MONTHS.get(m.group(3).lower()[:3])
        day2   = int(m.group(4))
        if month2:
            return datetime(scrape_year, month2, day2)

    # ── Format B: "Month Day - Day"  e.g. "May 04 - 07"
    m = re.search(r'([A-Za-z]+)\.?\s+(\d{1,2})\s*-\s*(\d{1,2})', delivery)
    if m:
        month = MONTHS.get(m.group(1).lower()[:3])
        day2  = int(m.group(3))
        if month:
            return datetime(scrape_year, month, day2)

    # ── Format C: "Get it before Weekday, Month Day - Day"
    m = re.search(
        r'(?:before\s+\w+,\s*)?([A-Za-z]+)\.?\s+(\d{1,2})\s*-\s*(\d{1,2})',
        delivery, re.IGNORECASE
    )
    if m:
        month = MONTHS.get(m.group(1).lower()[:3])
        day2  = int(m.group(3))
        if month:
            return datetime(scrape_year, month, day2)

    return None


def parse_price_to_eur(price_str: str) -> float | None:
    """Strips currency symbol/text, converts PLN → EUR, returns float (2 dp)."""
    if not price_str:
        return None
    # Keep only digits, commas, and dots
    cleaned = re.sub(r'[^\d,\.]', '', price_str)
    # Polish format uses comma as decimal separator: "3,68" → 3.68
    cleaned = cleaned.replace(',', '.')
    try:
        pln = float(cleaned)
        return round(pln * PLN_TO_EUR, 2)
    except ValueError:
        return None


# ── Load ──────────────────────────────────────────────────────────────────────
input_path = Path(args.input)
if not input_path.exists():
    raise FileNotFoundError(f"Input file not found: {input_path}")

data     = json.loads(input_path.read_text(encoding="utf-8"))
products = data.get("results", [])

print(f"[INFO] Loaded {len(products)} products from {input_path}\n")

# ── Filter ────────────────────────────────────────────────────────────────────
kept        = []
rejected    = {"no_rating": 0, "low_rating": 0, "no_delivery": 0, "too_far": 0, "no_price": 0}

for p in products:
    pid = p.get("id", "?")

    # 1. Rating check
    rating_raw = p.get("rating")
    if rating_raw is None:
        rejected["no_rating"] += 1
        print(f"  [SKIP] {pid} — no rating")
        continue
    try:
        rating = float(rating_raw)
    except (ValueError, TypeError):
        rejected["no_rating"] += 1
        print(f"  [SKIP] {pid} — unparseable rating: {rating_raw!r}")
        continue
    if rating < MIN_RATING:
        rejected["low_rating"] += 1
        print(f"  [SKIP] {pid} — rating {rating} < {MIN_RATING}")
        continue

    # 2. Delivery gap check
    scraped_at_str = p.get("scraped_at", "")
    try:
        scrape_date = datetime.fromisoformat(scraped_at_str)
    except (ValueError, TypeError):
        rejected["no_delivery"] += 1
        print(f"  [SKIP] {pid} — bad scraped_at: {scraped_at_str!r}")
        continue

    delivery_str  = p.get("delivery")
    later_date    = parse_later_delivery_date(delivery_str, scrape_date.year)

    if later_date is None:
        rejected["no_delivery"] += 1
        print(f"  [SKIP] {pid} — cannot parse delivery: {delivery_str!r}")
        continue

    gap_days = (later_date - scrape_date.replace(hour=0, minute=0, second=0, microsecond=0)).days
    if gap_days > MAX_DAYS:
        rejected["too_far"] += 1
        print(f"  [SKIP] {pid} — delivery gap {gap_days}d > {MAX_DAYS}d ({delivery_str})")
        continue

    # 3. Price conversion
    price_eur = parse_price_to_eur(p.get("price"))
    if price_eur is None:
        rejected["no_price"] += 1
        print(f"  [SKIP] {pid} — cannot parse price: {p.get('price')!r}")
        continue

    # ── Build filtered product entry ──────────────────────────────────────────
    filtered_product = {
        **p,
        "rating":    rating,
        "price_eur": price_eur,      # new int field in EUR
        "gap_days":  gap_days,       # handy for debugging
    }
    kept.append(filtered_product)
    print(f"  [KEEP] {pid} — rating={rating}, gap={gap_days}d, price={price_eur}€")


# ── Save ──────────────────────────────────────────────────────────────────────
output_path = Path(args.output)
output_data = {
    "last_updated": datetime.now().isoformat(),
    "total":        len(kept),
    "results":      kept,
}
output_path.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
print("FILTER SUMMARY")
print("=" * 50)
print(f"  Input products  : {len(products)}")
print(f"  Kept            : {len(kept)}")
print(f"  Rejected        : {len(products) - len(kept)}")
print(f"    No rating     : {rejected['no_rating']}")
print(f"    Low rating    : {rejected['low_rating']}")
print(f"    No delivery   : {rejected['no_delivery']}")
print(f"    Too far (>{MAX_DAYS}d): {rejected['too_far']}")
print(f"    No price      : {rejected['no_price']}")
print(f"\n  Saved to        : {output_path.resolve()}")
