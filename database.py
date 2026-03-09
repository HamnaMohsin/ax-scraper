import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

# -----------------------------
# Paths & Folders


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(BASE_DIR, "data", "products.db")
)

DATABASE_URL = f"sqlite:///{DB_PATH}"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)
# -----------------------------
# Session maker
# -----------------------------
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# -----------------------------
# Base class for models
# -----------------------------
Base = declarative_base()

# -----------------------------
# Dependency for FastAPI
# -----------------------------
# Use this with Depends(get_db) in endpoints
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# add this entire function at the bottom of database.py:
def run_migrations():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(products)"))
        existing_cols = {row[1] for row in result.fetchall()}

        if "exported_at" not in existing_cols:
            conn.execute(text("ALTER TABLE products ADD COLUMN exported_at DATETIME"))
            conn.commit()
            print("Migration: added 'exported_at' column to products ✅")
        else:
            print("Migration: 'exported_at' already exists, skipping ✅")
