"""
translate_endpoint.py
─────────────────────
Drop this file next to main.py and add to main.py:

    from translate_endpoint import router as translate_router
    app.include_router(translate_router)

Endpoint:
    POST /translate/{product_id}

Flow:
  1. Look up product_id in product_refined.
  2. Verify enhanced_title and enhanced_description are not NULL.
  3. Create table product_translations if it doesn't exist yet.
  4. If a row for this product_id already exists → return the cached result.
  5. Otherwise call gpt-4o once (all 5 languages in one shot) and persist.

Table: product_translations
Columns:
    id               INTEGER PK autoincrement
    product_id       BIGINT  FK → product_refined.product_id  UNIQUE
    title_romanian           TEXT
    description_romanian     TEXT
    title_german             TEXT
    description_german       TEXT
    title_portuguese         TEXT
    description_portuguese   TEXT
    title_finnish            TEXT
    description_finnish      TEXT
    title_french             TEXT
    description_french       TEXT
"""

import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, BigInteger, Integer, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, Session
from openai import OpenAI

# ── Re-use the app's database session if available, else fall back to env var ──
# If you already have `from database import get_db` in main.py, replace the
# get_db import below with your own and remove the fallback engine block.

from database import get_db, SessionLocal, engine as _engine
from models import ProductRefined as _ProductRefined   # reuse the existing mapped class

# ── OpenAI ────────────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL          = "gpt-4o"
RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5  # seconds

client = OpenAI()

# ── Languages ─────────────────────────────────────────────────────────────────

LANGUAGES = ["romanian", "german", "portuguese", "finnish", "french"]

# ── SQLAlchemy model for the new translations table ───────────────────────────

Base = declarative_base()


class ProductTranslation(Base):
    __tablename__ = "product_translations"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        BigInteger,
        ForeignKey("product_refined.product_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # ── Per-language columns (title + description) ────────────────────────────
    title_romanian         = Column(Text, nullable=True)
    description_romanian   = Column(Text, nullable=True)

    title_german           = Column(Text, nullable=True)
    description_german     = Column(Text, nullable=True)

    title_portuguese       = Column(Text, nullable=True)
    description_portuguese = Column(Text, nullable=True)

    title_finnish          = Column(Text, nullable=True)
    description_finnish    = Column(Text, nullable=True)

    title_french           = Column(Text, nullable=True)
    description_french     = Column(Text, nullable=True)


# Create the translations table on first import using raw SQL (idempotent).
# We avoid Base.metadata.create_all() here because product_refined belongs to
# a different Base (in models.py), and SQLAlchemy can't resolve the FK at DDL
# time across two separate metadata objects.
from sqlalchemy import text as _text

with _engine.connect() as _conn:
    _conn.execute(_text("""
        CREATE TABLE IF NOT EXISTS product_translations (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id             INTEGER NOT NULL UNIQUE,
            title_romanian         TEXT,
            description_romanian   TEXT,
            title_german           TEXT,
            description_german     TEXT,
            title_portuguese       TEXT,
            description_portuguese TEXT,
            title_finnish          TEXT,
            description_finnish    TEXT,
            title_french           TEXT,
            description_french     TEXT,
            FOREIGN KEY (product_id) REFERENCES product_refined(product_id) ON DELETE CASCADE
        )
    """))
    _conn.commit()

# ── Translation helper ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a professional product translator.
You will receive a product title and description in English.
Translate BOTH fields into each of the 5 requested languages.

Respond ONLY with a valid JSON object — no markdown, no extra text.
Structure:
{
  "romanian":   { "title": "...", "description": "..." },
  "german":     { "title": "...", "description": "..." },
  "portuguese": { "title": "...", "description": "..." },
  "finnish":    { "title": "...", "description": "..." },
  "french":     { "title": "...", "description": "..." }
}
""".strip()


def _call_openai(title: str, description: str) -> dict:
    """One gpt-4o call → translations for all 5 languages."""
    payload = json.dumps(
        {"title": title, "description": description},
        ensure_ascii=False,
    )

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": payload},
                ],
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)

        except Exception as exc:
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"OpenAI translation failed after {RETRY_ATTEMPTS} attempts: {exc}"
                )


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["translations"])


@router.post("/translate/{product_id}")
def translate_product(product_id: int, db: Session = Depends(get_db)):
    """
    Translate a product's enhanced_title and enhanced_description into
    Romanian, German, Portuguese, Finnish, and French.

    - 404 if product_id not found in product_refined.
    - 422 if enhanced_title or enhanced_description is NULL.
    - Returns cached result immediately if already translated.
    - Otherwise calls gpt-4o (single call, all 5 languages) and persists.
    """

    # ── 1. Fetch from product_refined ─────────────────────────────────────────
    refined = db.query(_ProductRefined).filter_by(product_id=product_id).first()

    if not refined:
        raise HTTPException(
            status_code=404,
            detail=f"product_id {product_id} not found in product_refined.",
        )

    if not refined.enhanced_title or not refined.enhanced_description:
        raise HTTPException(
            status_code=422,
            detail=(
                f"product_id {product_id} has NULL enhanced_title or "
                "enhanced_description — run /refine first."
            ),
        )

    # ── 2. Return cache if already translated ─────────────────────────────────
    existing = db.query(ProductTranslation).filter_by(product_id=product_id).first()
    if existing:
        return _row_to_response(product_id, existing, cached=True)

    # ── 3. Call OpenAI ────────────────────────────────────────────────────────
    try:
        translations = _call_openai(
            title       = refined.enhanced_title,
            description = refined.enhanced_description,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # ── 4. Persist — one loop over all languages ──────────────────────────────
    row_data = {"product_id": product_id}
    for lang in LANGUAGES:
        lang_result = translations.get(lang, {})
        row_data[f"title_{lang}"]       = lang_result.get("title")
        row_data[f"description_{lang}"] = lang_result.get("description")

    new_row = ProductTranslation(**row_data)
    db.add(new_row)
    db.commit()
    db.refresh(new_row)

    return _row_to_response(product_id, new_row, cached=False)


# ── Response builder ──────────────────────────────────────────────────────────

def _row_to_response(product_id: int, row: ProductTranslation, cached: bool) -> dict:
    languages = {}
    for lang in LANGUAGES:
        languages[lang] = {
            "title":       getattr(row, f"title_{lang}"),
            "description": getattr(row, f"description_{lang}"),
        }
    return {
        "product_id":  product_id,
        "cached":      cached,
        "translations": languages,
    }
