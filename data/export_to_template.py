"""
export_to_template.py
----------------------
Reads all products from the database, groups them by assigned Octopia category,
and writes one .xlsm file per category using the official Octopia template.

Usage:
    python3 export_to_template.py
    python3 export_to_template.py --template pdt_template_fr-FR_20260305_090255.xlsm --db data/products.db --out output_templates/
"""

import argparse
import json
import os
import shutil
import sqlite3

import pandas as pd
from openpyxl import load_workbook


# ── Column mapping (1-based, confirmed from template Row 4 + Row 5) ───────────
#
#  Col  Letter  Field code               Human label
#  ---  ------  -----------------------  ----------------------------
#   2     B     sellerProductReference   Référence vendeur* (max 50 chars)
#   3     C     title                    Titre*             (max 132 chars)
#   4     D     description              Description*       (max 2000 chars)
#   5     E     sellerPictureUrls_1      URL image 1*
#  10     J     sellerPictureUrls_2      URL image 2
#  11     K     sellerPictureUrls_3      URL image 3
#  12     L     sellerPictureUrls_4      URL image 4
#  13     M     sellerPictureUrls_5      URL image 5
#  14     N     sellerPictureUrls_6      URL image 6
#
# NOTE: The template only supports 6 image slots total (cols E, J–N).
# Any images beyond the 6th are silently dropped — this is a hard Octopia limit.

COLUMN_MAP = {
    "sellerProductReference": 2,
    "title":                  3,
    "description":            4,
    "image_1":                5,
    "image_2":               10,
    "image_3":               11,
    "image_4":               12,
    "image_5":               13,
    "image_6":               14,
}

# Octopia character limits (from Row 8 of the template)
REF_MAX         = 50
TITLE_MAX       = 132
DESCRIPTION_MAX = 2000

# Row where product data starts (rows 1–10 are template headers)
DATA_START_ROW = 11


# ── DB helpers ────────────────────────────────────────────────────────────────

def load_products(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    query = """
        SELECT
            pf.product_id,
            pf.url,
            pf.title              AS original_title,
            pf.description        AS original_description,
            pf.images,
            pr.enhanced_title,
            pr.enhanced_description,
            ca.assigned_category,
            ca.category_id,
            ca.llm_predicted_category,
            ca.similarity_score
        FROM product_fetched pf
        LEFT JOIN product_refined    pr ON pf.product_id = pr.product_id
        LEFT JOIN category_assignment ca ON pf.product_id = ca.product_id
        WHERE ca.category_id IS NOT NULL
        ORDER BY ca.category_id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def parse_images(images_raw) -> list:
    """
    Parse JSON images field from DB into a clean list of URLs.

    Strips query parameters (?width=...&height=...&hash=...) that
    Octopia's URL validator rejects. Octopia requires clean direct
    image URLs with no query strings.
    """
    if not images_raw:
        return []

    if isinstance(images_raw, list):
        imgs = images_raw
    else:
        try:
            imgs = json.loads(images_raw)
            if not isinstance(imgs, list):
                return []
        except Exception:
            return []

    clean = []
    for url in imgs:
        if url:
            # Remove ?width=...&height=...&hash=... query strings
            clean_url = url.split("?")[0].strip()
            if clean_url:
                clean.append(clean_url)
    return clean


# ── Template writer ───────────────────────────────────────────────────────────

def write_category_file(
    template_path: str,
    out_path: str,
    category_code: str,
    category_name: str,
    products: pd.DataFrame,
):
    """
    Copy the Octopia template, update category header in Row 1,
    and fill one product per row starting at Row 11.
    """
    shutil.copy2(template_path, out_path)

    wb = load_workbook(out_path, keep_vba=True)
    ws = wb.active

    # ── Row 1: update category code (C1) and category name (D1) ──────────────
    ws.cell(row=1, column=3).value = category_code
    ws.cell(row=1, column=4).value = category_name

    # ── Rename sheet (Excel max 31 chars, no slashes or special chars) ────────
    safe_name = (category_name[:31]
                 .replace("/", "-")
                 .replace("\\", "-")
                 .replace("*", "")
                 .replace("?", "")
                 .replace(":", "")
                 .replace("[", "")
                 .replace("]", ""))
    ws.title = safe_name

    # ── Write products starting at DATA_START_ROW ─────────────────────────────
    for row_offset, (_, product) in enumerate(products.iterrows()):
        excel_row = DATA_START_ROW + row_offset

        # Prefer LLM-refined content, fall back to raw scraped content
        title       = str(product.get("enhanced_title")       or product.get("original_title")       or "")
        description = str(product.get("enhanced_description") or product.get("original_description") or "")
        product_id  = str(product.get("product_id") or "")
        images      = parse_images(product.get("images"))

        # Enforce Octopia character limits
        product_id  = product_id[:REF_MAX]
        title       = title[:TITLE_MAX].rstrip()
        description = description[:DESCRIPTION_MAX].rstrip()

        # ── Write fields ──────────────────────────────────────────────────────
        ws.cell(row=excel_row, column=COLUMN_MAP["sellerProductReference"]).value = product_id
        ws.cell(row=excel_row, column=COLUMN_MAP["title"]).value                  = title
        ws.cell(row=excel_row, column=COLUMN_MAP["description"]).value            = description

        # ── Write image URLs (template supports max 6) ────────────────────────
        img_cols = ["image_1", "image_2", "image_3", "image_4", "image_5", "image_6"]
        for i, key in enumerate(img_cols):
            if i < len(images):
                ws.cell(row=excel_row, column=COLUMN_MAP[key]).value = images[i]

        # Warn if product has more than 6 images (extras are dropped)
        if len(images) > 6:
            print(f"    ⚠  product {product_id} has {len(images)} images — only first 6 written (template limit)")

    wb.save(out_path)
    print(f"  ✅ {len(products)} product(s) → {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export products to Octopia template files")
    parser.add_argument("--template", default="pdt_template_fr-FR_20260305_090255.xlsm")
    parser.add_argument("--db",       default="products.db")
    parser.add_argument("--out",      default="output_templates")
    args = parser.parse_args()

    if not os.path.exists(args.template):
        print(f"❌ Template not found: {args.template}")
        return
    if not os.path.exists(args.db):
        print(f"❌ Database not found: {args.db}")
        return

    os.makedirs(args.out, exist_ok=True)

    print(f"Loading products from {args.db}...")
    df = load_products(args.db)

    if df.empty:
        print("❌ No categorized products found in database.")
        print("   Run /scrape-only → /refine → /assign-category first.")
        return

    total_categories = df["category_id"].nunique()
    print(f"Found {len(df)} products across {total_categories} categories\n")

    for category_id, group in df.groupby("category_id"):
        category_name = group.iloc[0]["assigned_category"] or str(category_id)
        safe_filename = str(category_id).replace("/", "_").replace(" ", "_")
        out_path = os.path.join(args.out, f"{safe_filename}.xlsm")

        print(f"Category [{category_id}] {category_name} — {len(group)} product(s)")
        write_category_file(
            template_path=args.template,
            out_path=out_path,
            category_code=str(category_id),
            category_name=str(category_name),
            products=group,
        )

    print(f"\n✅ Done. {total_categories} file(s) written to '{args.out}/'")


if __name__ == "__main__":
    main()