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
  3. If a row for this product_id already exists → return the cached result.
  4. Otherwise call gpt-4o once (all 5 languages in one shot) and persist.

Table: product_translations
Columns:
    id                        INTEGER PK autoincrement
    product_id                BIGINT  FK → product_refined.product_id  UNIQUE
    title_romanian            TEXT
    description_romanian      TEXT
    specifications_romanian   JSON
    title_german              TEXT
    description_german        TEXT
    specifications_german     JSON
    title_portuguese          TEXT
    description_portuguese    TEXT
    specifications_portuguese JSON
    title_finnish             TEXT
    description_finnish       TEXT
    specifications_finnish    JSON
    title_french              TEXT
    description_french        TEXT
    specifications_french     JSON
"""

import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from openai import OpenAI

from database import get_db
from models import ProductRefined as _ProductRefined, ProductTranslation, ProductFetched

# ── OpenAI ────────────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL          = "gpt-4o"
RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5  # seconds

client = OpenAI(api_key=OPENAI_API_KEY)

# ── Languages ─────────────────────────────────────────────────────────────────

LANGUAGES = ["romanian", "german", "portuguese", "finnish", "french"]

# ── Translation helper ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a professional product translator.
You will receive a product title, description, and specifications (key-value pairs) in English.
Translate ALL fields into each of the 5 requested languages.

For specifications, translate BOTH the keys and the values.

Respond ONLY with a valid JSON object — no markdown, no extra text.
Structure:
{
  "romanian":   { "title": "...", "description": "...", "specifications": { "translated_key": "translated_value", ... } },
  "german":     { "title": "...", "description": "...", "specifications": { "translated_key": "translated_value", ... } },
  "portuguese": { "title": "...", "description": "...", "specifications": { "translated_key": "translated_value", ... } },
  "finnish":    { "title": "...", "description": "...", "specifications": { "translated_key": "translated_value", ... } },
  "french":     { "title": "...", "description": "...", "specifications": { "translated_key": "translated_value", ... } }
}

If specifications is empty or null, return an empty object {} for that field.
""".strip()


def _call_openai(title: str, description: str, specifications: dict) -> dict:
    """One gpt-4o call → translations for all 5 languages including specifications."""
    payload = json.dumps(
        {
            "title":          title,
            "description":    description,
            "specifications": specifications or {},
        },
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
    Translate a product's enhanced_title, enhanced_description, and specifications
    into Romanian, German, Portuguese, Finnish, and French.

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

    # ── 3. Fetch specifications from product_fetched ──────────────────────────
    fetched = db.query(ProductFetched).filter_by(product_id=product_id).first()
    specifications = (fetched.specifications or {}) if fetched else {}

    # ── 4. Call OpenAI ────────────────────────────────────────────────────────
    try:
        translations = _call_openai(
            title          = refined.enhanced_title,
            description    = refined.enhanced_description,
            specifications = specifications,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # ── 5. Persist — one loop over all languages ──────────────────────────────
    row_data = {"product_id": product_id}
    for lang in LANGUAGES:
        lang_result = translations.get(lang, {})
        row_data[f"title_{lang}"]          = lang_result.get("title")
        row_data[f"description_{lang}"]    = lang_result.get("description")
        row_data[f"specifications_{lang}"] = lang_result.get("specifications") or {}

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
            "title":          getattr(row, f"title_{lang}"),
            "description":    getattr(row, f"description_{lang}"),
            "specifications": getattr(row, f"specifications_{lang}") or {},
        }
    return {
        "product_id":   product_id,
        "cached":       cached,
        "translations": languages,
    }
