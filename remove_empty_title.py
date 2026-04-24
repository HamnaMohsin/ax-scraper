import json
import os
import re
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


MIN_TITLE_LEN = 50

def filter_no_title(input_file: str, output_file: str) -> None:
    print("=" * 60)
    print("🧹  Filter — removing products with no/short title")
    print(f"    Min title length: {MIN_TITLE_LEN} characters")
    print("=" * 60)

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_original = 0
    total_removed  = 0
    filtered_results = {}

    JUNK_TITLE = re.compile(r'^[\s\W_]+$')

    for cat_name, cat_data in data["results"].items():
        products = cat_data["products"]
        kept     = []
        removed  = 0

        for product in products:
            title = product.get("title", "").strip()
            if title and not JUNK_TITLE.match(title) and len(title) >= MIN_TITLE_LEN:
                kept.append(product)
            else:
                removed += 1

        total_original += len(products)
        total_removed  += removed

        filtered_results[cat_name] = {
            **cat_data,
            "products": kept,
            "stats": {
                "original_count": len(products),
                "filtered_count": len(kept),
                "removed_count":  removed,
            },
        }

        print(f"📂  '{cat_name}': {len(products)} → {len(kept)} (-{removed} no/short-title)")

    total_kept = total_original - total_removed

    output_data = {
        **data,
        "metadata": {
            **data.get("metadata", {}),
            "total_original":      total_original,
            "total_filtered":      total_kept,
            "total_removed_no_title": total_removed,  # includes short titles (<50 chars)
        },
        "results": filtered_results,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("✅  DONE")
    print(f"    Original  : {total_original:,}")
    print(f"    Kept      : {total_kept:,}")
    print(f"    Removed   : {total_removed:,} (no/short title, <{MIN_TITLE_LEN} chars)")
    print(f"    Saved to  : {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    INPUT_FILE  = "aliexpress_products.json"
    OUTPUT_FILE = "aliexpress_products.json"   # overwrites in-place

    filter_no_title(INPUT_FILE, OUTPUT_FILE)
