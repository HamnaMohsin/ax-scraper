"""
main.py — AliExpress Scraper FastAPI application
Base URL: http://34.10.186.46:8001
"""

import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import ProductFetched, ProductRefined, CategoryAssignment
from schemas import (
    ScrapeRequest,
    CategorizeRequest,
    ProductFetchedOut,
    ProductRefinedOut,
    CategoryAssignmentOut,
    ProductFullOut,
)
from scraper2       import extract_aliexpress_product
from llm_refiner2   import refine_product
from assign_embeddings2 import assign_category

from data.export_to_template import (
    load_products,
    write_category_file,
    make_safe_filename,
)

# ── App init ──────────────────────────────────────────────────────────────────

app = FastAPI(title="AX-Scraper", version="1.0")

OUT_DIR = os.path.join(os.path.dirname(__file__), "data", "output_templates")
os.makedirs(OUT_DIR, exist_ok=True)


@app.on_event("startup")
def startup():
    init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_full_out(p: ProductFetched) -> ProductFullOut:
    return ProductFullOut(
        product_id=p.product_id,
        url=p.url,
        title=p.title,
        description=p.description,
        images=p.images,
        exported_at=p.exported_at,
        enhanced_title        = p.refined.enhanced_title        if p.refined else None,
        enhanced_description  = p.refined.enhanced_description  if p.refined else None,
        description_marketing = p.refined.description_marketing if p.refined else None,
        llm_predicted_category= p.category.llm_predicted_category if p.category else None,
        assigned_category     = p.category.assigned_category      if p.category else None,
        category_id           = p.category.category_id            if p.category else None,
        similarity_score      = p.category.similarity_score       if p.category else None,
    )


def _upsert(db: Session, url: str, data: dict) -> ProductFetched:
    """
    Insert or update product_fetched, then refine + categorize.
    data = { title, description_text, images }
    """
    product = db.query(ProductFetched).filter_by(url=url).first()

    if product:
        product.title       = data["title"]
        product.description = data["description_text"]
        product.images      = data["images"]
        product.exported_at = None   # reset so it gets re-exported
    else:
        product_id = int(url.split("/item/")[-1].split(".")[0].split("?")[0])
        product = ProductFetched(
            product_id  = product_id,
            url         = url,
            title       = data["title"],
            description = data["description_text"],
            images      = data["images"],
            exported_at = None,
        )
        db.add(product)

    db.flush()
    product_id = product.product_id

    # ── Refine ────────────────────────────────────────────────────────────────
    refined = refine_product(data["title"], data["description_text"])

    refined_row = db.query(ProductRefined).filter_by(product_id=product_id).first()
    if refined_row:
        refined_row.enhanced_title        = refined["refined_title"]
        refined_row.enhanced_description  = refined["refined_description"]
        refined_row.description_marketing = refined["description_marketing"]
    else:
        db.add(ProductRefined(
            product_id            = product_id,
            enhanced_title        = refined["refined_title"],
            enhanced_description  = refined["refined_description"],
            description_marketing = refined["description_marketing"],
        ))

    db.flush()

    # ── Categorize ────────────────────────────────────────────────────────────
    category_result = assign_category(
        refined["refined_title"],
        refined["refined_description"],
    )

    cat_row = db.query(CategoryAssignment).filter_by(product_id=product_id).first()
    if cat_row:
        cat_row.llm_predicted_category = category_result.get("llm_predicted_category")
        cat_row.assigned_category      = category_result.get("category_path")
        cat_row.category_id            = category_result.get("category_id")
        cat_row.similarity_score       = category_result.get("similarity_score")
    else:
        db.add(CategoryAssignment(
            product_id             = product_id,
            llm_predicted_category = category_result.get("llm_predicted_category"),
            assigned_category      = category_result.get("category_path"),
            category_id            = category_result.get("category_id"),
            similarity_score       = category_result.get("similarity_score"),
        ))

    db.commit()
    db.refresh(product)
    return product


def _scrape_and_save(db: Session, url: str) -> dict:
    """Scrape only — no LLM, no categorization. Resets exported_at."""
    data = extract_aliexpress_product(url)
    if not data.get("title"):
        return {"url": url, "success": False, "error": "Scrape failed or blocked"}

    product = db.query(ProductFetched).filter_by(url=url).first()
    if product:
        product.title       = data["title"]
        product.description = data["description_text"]
        product.images      = data["images"]
        product.exported_at = None
    else:
        product_id = int(url.split("/item/")[-1].split(".")[0].split("?")[0])
        product = ProductFetched(
            product_id  = product_id,
            url         = url,
            title       = data["title"],
            description = data["description_text"],
            images      = data["images"],
            exported_at = None,
        )
        db.add(product)

    db.commit()
    db.refresh(product)

    return {
        "url":         url,
        "success":     True,
        "product_id":  product.product_id,
        "title":       product.title,
        "description": product.description,
        "images":      product.images,
    }


def _run_export(only_new: bool = False) -> dict:
    """Core export logic shared by /export-templates endpoint."""
    categories = load_products(only_new=only_new)

    if not categories:
        return {
            "mode":             "incremental" if only_new else "full",
            "total_products":   0,
            "total_categories": 0,
            "output_dir":       OUT_DIR,
            "files":            [],
        }

    all_written_ids = []
    files_summary   = []

    for cat_id, cat_data in categories.items():
        cat_name  = cat_data["category_name"]
        products  = cat_data["products"]
        safe_name = make_safe_filename(str(cat_id), cat_name)
        out_path  = os.path.join(OUT_DIR, f"{safe_name}.xlsm")

        written_ids = write_category_file(
            category_id   = cat_id,
            category_name = cat_name,
            products      = products,
            out_path      = out_path,
            append_mode   = only_new,
        )
        all_written_ids.extend(written_ids)

        files_summary.append({
            "category_id":    cat_id,
            "category_name":  cat_name,
            "product_count":  len(written_ids),
            "file":           out_path,
            "products": [
                {
                    "product_id":       str(p["product_id"]),
                    "title":            (p["enhanced_title"] or p["original_title"])[:80],
                    "image_count":      len(p["images"]),
                    "has_description":  bool(p["enhanced_description"] or p["original_description"]),
                }
                for p in products
            ],
        })

    # Mark exported_at in DB
    if all_written_ids:
        from database import SessionLocal
        from sqlalchemy import update
        db_write = SessionLocal()
        try:
            now = datetime.utcnow()
            db_write.query(ProductFetched).filter(
                ProductFetched.product_id.in_([int(i) for i in all_written_ids])
            ).update({"exported_at": now}, synchronize_session=False)
            db_write.commit()
            print(f"Marked {len(all_written_ids)} product(s) as exported.")
        finally:
            db_write.close()

    return {
        "mode":             "incremental" if only_new else "full",
        "total_products":   len(all_written_ids),
        "total_categories": len(files_summary),
        "output_dir":       OUT_DIR,
        "files":            files_summary,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/scrape")
def scrape_full(request: ScrapeRequest, db: Session = Depends(get_db)):
    """Full pipeline: scrape → refine → categorize → save."""
    urls = [u.strip() for u in request.urls.split(",") if u.strip()]

    results = []
    success_count = 0
    fail_count    = 0

    for url in urls:
        try:
            data = extract_aliexpress_product(url)
            if not data.get("title"):
                fail_count += 1
                results.append({"url": url, "success": False, "error": "Scrape failed or blocked"})
                continue

            product = _upsert(db, url, data)
            success_count += 1
            results.append({
                "url":              url,
                "success":          True,
                "product_id":       product.product_id,
                "original_title":   product.title,
                "enhanced_title":   product.refined.enhanced_title   if product.refined  else None,
                "assigned_category":product.category.assigned_category if product.category else None,
                "category_id":      product.category.category_id       if product.category else None,
                "similarity_score": product.category.similarity_score  if product.category else None,
                "images":           product.images,
            })
        except Exception as e:
            fail_count += 1
            results.append({"url": url, "success": False, "error": str(e)})

    return {"total": len(urls), "success": success_count, "failed": fail_count, "results": results}


@app.post("/scrape-only")
def scrape_only(request: ScrapeRequest, db: Session = Depends(get_db)):
    """Scrape only — saves raw data, no LLM or categorization."""
    urls = [u.strip() for u in request.urls.split(",") if u.strip()]

    results = []
    success_count = 0
    fail_count    = 0

    for url in urls:
        try:
            result = _scrape_and_save(db, url)
            if result["success"]:
                success_count += 1
            else:
                fail_count += 1
            results.append(result)
        except Exception as e:
            fail_count += 1
            results.append({"url": url, "success": False, "error": str(e)})

    return {"total": len(urls), "success": success_count, "failed": fail_count, "results": results}


@app.post("/refine/{product_id}")
def refine(product_id: int, db: Session = Depends(get_db)):
    """Step 2: refine an already-scraped product with the LLM."""
    product = db.query(ProductFetched).filter_by(product_id=product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    refined = refine_product(product.title or "", product.description or "")

    refined_row = db.query(ProductRefined).filter_by(product_id=product_id).first()
    if refined_row:
        refined_row.enhanced_title        = refined["refined_title"]
        refined_row.enhanced_description  = refined["refined_description"]
        refined_row.description_marketing = refined["description_marketing"]
    else:
        refined_row = ProductRefined(
            product_id            = product_id,
            enhanced_title        = refined["refined_title"],
            enhanced_description  = refined["refined_description"],
            description_marketing = refined["description_marketing"],
        )
        db.add(refined_row)

    db.commit()
    db.refresh(refined_row)
    return ProductRefinedOut.model_validate(refined_row)


@app.post("/assign-category/{product_id}")
def assign_cat(product_id: int, db: Session = Depends(get_db)):
    """Step 3: assign Octopia category via embedding similarity."""
    refined_row = db.query(ProductRefined).filter_by(product_id=product_id).first()
    if not refined_row:
        raise HTTPException(status_code=404, detail="No refined product found — run /refine first")

    result = assign_category(
        refined_row.enhanced_title       or "",
        refined_row.enhanced_description or "",
    )

    cat_row = db.query(CategoryAssignment).filter_by(product_id=product_id).first()
    if cat_row:
        cat_row.llm_predicted_category = result.get("llm_predicted_category")
        cat_row.assigned_category      = result.get("category_path")
        cat_row.category_id            = result.get("category_id")
        cat_row.similarity_score       = result.get("similarity_score")
    else:
        cat_row = CategoryAssignment(
            product_id             = product_id,
            llm_predicted_category = result.get("llm_predicted_category"),
            assigned_category      = result.get("category_path"),
            category_id            = result.get("category_id"),
            similarity_score       = result.get("similarity_score"),
        )
        db.add(cat_row)

    db.commit()
    db.refresh(cat_row)
    return CategoryAssignmentOut.model_validate(cat_row)


@app.post("/categorize")
def categorize_standalone(request: CategorizeRequest):
    """Standalone category lookup — does not touch the DB."""
    result = assign_category(request.title, request.description)
    return {
        "category_id":   result.get("category_id"),
        "category_path": result.get("category_path"),
        "similarity_score": result.get("similarity_score"),
    }


@app.post("/export-templates")
def export_templates(only_new: bool = Query(default=False)):
    """
    Export categorized products to per-category .xlsm files.
    only_new=false (default) = full rebuild.
    only_new=true = append only products not yet exported.
    """
    return _run_export(only_new=only_new)


# ── Read endpoints ─────────────────────────────────────────────────────────────

@app.get("/products", response_model=list[ProductFullOut])
def list_products(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    products = db.query(ProductFetched).offset(offset).limit(limit).all()
    return [_build_full_out(p) for p in products]


@app.get("/products/{product_id}", response_model=ProductFullOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductFetched).filter_by(product_id=product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return _build_full_out(p)


@app.get("/products/fetched", response_model=list[ProductFetchedOut])
def list_fetched(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    return db.query(ProductFetched).offset(offset).limit(limit).all()


@app.get("/products/fetched/{product_id}", response_model=ProductFetchedOut)
def get_fetched(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductFetched).filter_by(product_id=product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@app.get("/products/refined", response_model=list[ProductRefinedOut])
def list_refined(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    return db.query(ProductRefined).offset(offset).limit(limit).all()


@app.get("/products/refined/{product_id}", response_model=ProductRefinedOut)
def get_refined(product_id: int, db: Session = Depends(get_db)):
    r = db.query(ProductRefined).filter_by(product_id=product_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Product not found")
    return r


@app.delete("/products/fetched/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductFetched).filter_by(product_id=product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(p)
    db.commit()
    return {"message": f"Product {product_id} deleted (cascaded to all tables)"}
