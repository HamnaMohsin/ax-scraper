"""
export_to_template.py
----------------------
Reads categorized products from the database and writes one .xlsm per category
using the official Octopia template.

Two modes (controlled by caller):
  only_new=False  → Full rebuild: copy template fresh, write ALL products from row 11.
                    Overwrites any existing file.
  only_new=True   → Incremental: if file already exists, APPEND new products after
                    the last used row. If file does not exist yet, behaves like full mode.

Column mapping (1-based, confirmed from Octopia template):
  Col 2  (B)  sellerProductReference  max 50 chars
  Col 3  (C)  title                   max 132 chars
  Col 4  (D)  description             max 2000 chars
  Col 5  (E)  sellerPictureUrls_1     image 1 (required)
  Col 10 (J)  sellerPictureUrls_2     image 2
  Col 11 (K)  sellerPictureUrls_3     image 3
  Col 12 (L)  sellerPictureUrls_4     image 4
  Col 13 (M)  sellerPictureUrls_5     image 5
  Col 14 (N)  sellerPictureUrls_6     image 6

NOTE: Template only supports 6 image slots. Extras are dropped (hard Octopia limit).
"""

import argparse
import json
import os
import shutil
import sqlite3

import pandas as pd
from openpyxl import load_workbook


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

REF_MAX         = 50
TITLE_MAX       = 132
DESCRIPTION_MAX = 2000
DATA_START_ROW  = 11   # rows 1-10 are template headers


# ── DB helpers ─────────────────────────────────────────────────────────────────

def load_products(db_path: str, only_new: bool = False) -> pd.DataFrame:
    """
    Load categorized products from SQLite.
    only_new=True  → only rows where exported_at IS NULL
    only_new=False → all categorized products
    """
    conn = sqlite3.connect(db_path)

    where = "WHERE ca.category_id IS NOT NULL"
    if only_new:
        where += " AND pf.exported_at IS NULL"

    query = f"""
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
        {where}
        ORDER BY ca.category_id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def parse_images(images_raw) -> list:
    """
    Parse JSON images field and strip query params (?width=...&hash=...).
    Octopia rejects URLs with query strings.
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

    return [url.split("?")[0].strip() for url in imgs if url]


# ── Template writer ────────────────────────────────────────────────────────────

def write_category_file(
    template_path: str,
    out_path: str,
    category_code: str,
    category_name: str,
    products: pd.DataFrame,
    append: bool = False,
) -> list:
    """
    Write products into an Octopia .xlsm template file.

    append=False → copy template fresh, write all products from row 11.
    append=True  → open existing file and append after the last used row.
                   Falls back to fresh copy if file does not exist.

    Returns list of product_ids written (used to mark exported_at in DB).
    """
    file_exists = os.path.exists(out_path)

    if append and file_exists:
        # Open existing file and find next empty row
        wb = load_workbook(out_path, keep_vba=True)
        ws = wb.active
        next_row = DATA_START_ROW
        for row in range(DATA_START_ROW, ws.max_row + 2):
            if all(ws.cell(row, c).value is None for c in [2, 3, 5]):
                next_row = row
                break
        print(f"  Appending from row {next_row} (file had {next_row - DATA_START_ROW} product(s) already)")
    else:
        # Fresh copy from template
        shutil.copy2(template_path, out_path)
        wb = load_workbook(out_path, keep_vba=True)
        ws = wb.active

        # Update Row 1 category header
        ws.cell(row=1, column=3).value = category_code
        ws.cell(row=1, column=4).value = category_name

        # Rename sheet (Excel max 31 chars, no special chars)
        safe_name = (category_name[:31]
                     .replace("/", "-").replace("\\", "-")
                     .replace("*", "").replace("?", "")
                     .replace(":", "").replace("[", "").replace("]", ""))
        ws.title = safe_name
        next_row = DATA_START_ROW

    # Write products
    written_ids = []
    for row_offset, (_, product) in enumerate(products.iterrows()):
        excel_row = next_row + row_offset

        title       = str(product.get("enhanced_title")       or product.get("original_title")       or "")
        description = str(product.get("enhanced_description") or product.get("original_description") or "")
        product_id  = str(product.get("product_id") or "")
        images      = parse_images(product.get("images"))

        # Enforce Octopia character limits
        product_id  = product_id[:REF_MAX]
        title       = title[:TITLE_MAX].rstrip()
        description = description[:DESCRIPTION_MAX].rstrip()

        ws.cell(row=excel_row, column=COLUMN_MAP["sellerProductReference"]).value = product_id
        ws.cell(row=excel_row, column=COLUMN_MAP["title"]).value                  = title
        ws.cell(row=excel_row, column=COLUMN_MAP["description"]).value            = description

        img_keys = ["image_1", "image_2", "image_3", "image_4", "image_5", "image_6"]
        for i, key in enumerate(img_keys):
            if i < len(images):
                ws.cell(row=excel_row, column=COLUMN_MAP[key]).value = images[i]

        if len(images) > 6:
            print(f"    ⚠  product {product_id} has {len(images)} images — only first 6 written (template limit)")

        #written_ids.append(product.get("product_id"))
        written_ids.append(int(product.get("product_id")))


    wb.save(out_path)
    mode_label = "appended" if (append and file_exists) else "written"
    print(f"  ✅ {len(products)} product(s) {mode_label} → {out_path}")
    return written_ids


# ── Main (standalone CLI) ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export products to Octopia template files")
    parser.add_argument("--template", default="data/pdt_template_fr-FR_20260305_090255.xlsm")
    parser.add_argument("--db",       default="data/products.db")
    parser.add_argument("--out",      default="data/output_templates")
    parser.add_argument("--only-new", action="store_true",
                        help="Only export products not yet exported (exported_at IS NULL)")
    args = parser.parse_args()

    if not os.path.exists(args.template):
        print(f"❌ Template not found: {args.template}")
        return
    if not os.path.exists(args.db):
        print(f"❌ Database not found: {args.db}")
        return

    os.makedirs(args.out, exist_ok=True)
    df = load_products(args.db, only_new=args.only_new)

    if df.empty:
        print("❌ No products to export.")
        return

    total_categories = df["category_id"].nunique()
    print(f"Found {len(df)} product(s) across {total_categories} category/ies\n")

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
            append=args.only_new,
        )

    print(f"\n✅ Done. {total_categories} file(s) written to '{args.out}/'")


if __name__ == "__main__":
    main()
