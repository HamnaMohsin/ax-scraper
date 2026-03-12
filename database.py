from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./data/products.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Add new columns to existing tables if they don't exist yet."""
    with engine.connect() as conn:
        # ── product_fetched: exported_at ──────────────────────────────────────
        result = conn.execute(text("PRAGMA table_info(product_fetched)"))
        fetched_cols = {row[1] for row in result.fetchall()}

        if "exported_at" not in fetched_cols:
            conn.execute(text("ALTER TABLE product_fetched ADD COLUMN exported_at DATETIME"))
            conn.commit()
            print("Migration: added 'exported_at' to product_fetched ✅")

        # ── product_refined: description_marketing ────────────────────────────
        result2 = conn.execute(text("PRAGMA table_info(product_refined)"))
        refined_cols = {row[1] for row in result2.fetchall()}

        if "description_marketing" not in refined_cols:
            conn.execute(text("ALTER TABLE product_refined ADD COLUMN description_marketing TEXT"))
            conn.commit()
            print("Migration: added 'description_marketing' to product_refined ✅")


def init_db():
    import models  # noqa: F401 — registers all models with Base
    Base.metadata.create_all(bind=engine)
    run_migrations()
