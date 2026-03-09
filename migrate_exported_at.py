"""
migrate_add_exported_at.py
--------------------------
One-time migration script. Run on the VM to add the exported_at column
to the existing product_fetched table without losing any data.

Usage:
    cd /opt/ax-scraper
    python3 migrate_add_exported_at.py

Safe to run multiple times — skips if column already exists.
"""

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data", "products.db")


def migrate():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # Check existing columns
    cur.execute("PRAGMA table_info(product_fetched)")
    existing_cols = {row[1] for row in cur.fetchall()}
    print(f"Existing columns in product_fetched: {existing_cols}")

    if "exported_at" in existing_cols:
        print("✅ 'exported_at' column already exists — nothing to do.")
        conn.close()
        return

    # Add the column (NULL for all existing rows = not yet exported)
    cur.execute("ALTER TABLE product_fetched ADD COLUMN exported_at DATETIME")
    conn.commit()

    # Verify
    cur.execute("SELECT COUNT(*) FROM product_fetched")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM product_fetched WHERE exported_at IS NULL")
    nulls = cur.fetchone()[0]

    conn.close()

    print(f"✅ Migration complete.")
    print(f"   Column 'exported_at' added to product_fetched.")
    print(f"   {total} existing product(s) → all set to NULL (will be exported on next run).")
    print(f"   NULL count: {nulls} / {total}")


if __name__ == "__main__":
    migrate()
