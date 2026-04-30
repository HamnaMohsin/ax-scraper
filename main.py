"""
main.py — AliExpress Scraper FastAPI application
Base URL: http://34.10.186.46:8001
"""

import os
import json
import re
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import ProductFetched, ProductRefined, CategoryAssignment, ManufacturerInfo
from schemas import (
    ScrapeRequest,
    CategorizeRequest,
    ProductFetchedOut,
    ProductRefinedOut,
    CategoryAssignmentOut,
    CategoryStandaloneOut,
    ProductFullOut,
    ManufacturerInfoOut,
    ProductDetailsRequest,
    StoreScrapeByRangeRequest,
)
from scraper3 import extract_aliexpress_product
from llm_refiner2 import refine_product
from assign_embeddings2 import categorize_product as assign_category

from data.export_to_template import (
    load_products,
    write_category_file,
    make_safe_filename,
)

import uuid
import subprocess
from enum import Enum
import sys
import csv

from scr2 import (
    CATEGORIES as DEFAULT_CATEGORIES,
    MAX_PAGES_PER_CATEGORY,
    scrape_category,
)
from scr04 import scrape_product_details, scrape_product_details_bulk
from scr_item_count import (
    load_store_ids_from_csv,
    scrape_multiple_stores,
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
        category_id           = str(p.category.category_id) if p.category and p.category.category_id is not None else None,
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
    _upsert_manufacturer(db, data.get("store_info", {}), data.get("compliance_info", {}))

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
    _upsert_manufacturer(db, data.get("store_info", {}), data.get("compliance_info", {}))

    return {
        "url":             url,
        "success":         True,
        "product_id":      product.product_id,
        "title":           product.title,
        "description":     product.description,
        "images":          product.images,
        "store_info":      data.get("store_info", {}),
        "compliance_info": data.get("compliance_info", {}),
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
            "category_id":   cat_id,
            "category_name": cat_name,
            "product_count": len(written_ids),
            "file":          out_path,
            "products": [
                {
                    "product_id":      str(p["product_id"]),
                    "title":           (p["enhanced_title"] or p["original_title"])[:80],
                    "image_count":     len(p["images"]),
                    "has_description": bool(p["enhanced_description"] or p["original_description"]),
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
    fail_count = 0

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
                "url":               url,
                "success":           True,
                "product_id":        product.product_id,
                "original_title":    product.title,
                "enhanced_title":    product.refined.enhanced_title if product.refined else None,
                "assigned_category": product.category.assigned_category if product.category else None,
                "category_id":       product.category.category_id if product.category else None,
                "similarity_score":  product.category.similarity_score if product.category else None,
                "images":            product.images,
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


def _upsert_manufacturer(db: Session, store_info: dict, compliance_info: dict):
    store_name = store_info.get("Store Name") or store_info.get("Name", "")
    store_id   = store_info.get("Store no.", "")

    if not store_name or not store_id:
        return

    mfr_data = compliance_info.get("Manufacturer information", {})

    existing = db.query(ManufacturerInfo).filter_by(
        store_name=store_name, store_id=store_id
    ).first()

    if existing:
        existing.name    = mfr_data.get("Name",    existing.name)
        existing.address = mfr_data.get("Address", existing.address)
        existing.email   = mfr_data.get("Email",   existing.email)
        existing.phone   = mfr_data.get("Phone",   existing.phone)
    else:
        db.add(ManufacturerInfo(
            store_name = store_name,
            store_id   = store_id,
            name       = mfr_data.get("Name"),
            address    = mfr_data.get("Address"),
            email      = mfr_data.get("Email"),
            phone      = mfr_data.get("Phone"),
        ))
    db.commit()
    print(f"ManufacturerInfo upserted: {store_name} / {store_id}")


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


@app.get("/products/{product_id}", response_model=ProductFullOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductFetched).filter_by(product_id=product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return _build_full_out(p)


@app.get("/manufacturer", response_model=list[ManufacturerInfoOut])
def get_manufacturers(limit: int = 10, db: Session = Depends(get_db)):
    rows = db.query(ManufacturerInfo).limit(limit).all()
    return rows


# ── Category Scraper ──────────────────────────────────────────────────────────

_scraper_jobs: dict[str, dict] = {}


@app.post("/run-category-scraper", status_code=202)
def run_category_scraper(background_tasks: BackgroundTasks):
    """
    Runs scr2.py as a background process.
    Returns job_id immediately — poll /run-category-scraper/{job_id} for status.
    """
    job_id = str(uuid.uuid4())
    _scraper_jobs[job_id] = {
        "job_id":      job_id,
        "status":      "running",
        "started_at":  datetime.utcnow().isoformat(),
        "finished_at": None,
        "error":       None,
    }

    def _run(job_id: str):
        try:
            result = subprocess.run(
                [sys.executable, "scr2.py"],
                capture_output=True,
                text=True,
            )
            _scraper_jobs[job_id]["status"]      = "completed" if result.returncode == 0 else "failed"
            _scraper_jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
            if result.returncode != 0:
                _scraper_jobs[job_id]["error"] = result.stderr[-500:]
        except Exception as e:
            _scraper_jobs[job_id]["status"]      = "failed"
            _scraper_jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
            _scraper_jobs[job_id]["error"]       = str(e)

    background_tasks.add_task(_run, job_id)
    return {
        "job_id":   job_id,
        "status":   "running",
        "message":  "Scraper started. Poll /run-category-scraper/{job_id} for status.",
    }


@app.get("/run-category-scraper/{job_id}")
def get_scraper_job(job_id: str):
    """Poll status of a running category scraper job."""
    job = _scraper_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


# ── Product Details Endpoints (scr04) ─────────────────────────────────────────

@app.post("/product-details")
def get_product_details_bulk(
    request: ProductDetailsRequest,
    db: Session = Depends(get_db),
):
    """
    Scrape live rating, delivery, price, and quantity for a list of product IDs.
    Checks each ID exists in DB first (run /scrape or /scrape-only first).
    """
    missing = []
    for pid in request.ids:
        if not db.query(ProductFetched).filter_by(product_id=pid).first():
            missing.append(pid)

    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Products not found in DB (run /scrape first): {missing}",
        )

    summary = scrape_product_details_bulk(
        product_ids=request.ids,
        output_file=request.output_file,
    )

    return {
        "total":      summary["total"],
        "saved_to":   summary["saved_to"],
        "scraped_at": datetime.utcnow().isoformat(),
        "results": [
            {
                "product_id": r["id"],
                "url":        r.get("url"),
                "rating":     r.get("rating"),
                "delivery":   r.get("delivery"),
                "price":      r.get("price"),
                "quantity":   r.get("quantity"),
                "errors":     r.get("errors", []),
            }
            for r in summary["results"]
        ],
    }


@app.post("/product-details/raw")
def get_product_details_bulk_no_db(request: ProductDetailsRequest):
    """
    Scrape live rating, delivery, price, and quantity — no DB check required.
    """
    summary = scrape_product_details_bulk(
        product_ids=request.ids,
        output_file=request.output_file,
    )

    return {
        "total":      summary["total"],
        "saved_to":   summary["saved_to"],
        "scraped_at": datetime.utcnow().isoformat(),
        "results": [
            {
                "product_id": r["id"],
                "url":        r.get("url"),
                "rating":     r.get("rating"),
                "delivery":   r.get("delivery"),
                "price":      r.get("price"),
                "quantity":   r.get("quantity"),
                "errors":     r.get("errors", []),
            }
            for r in summary["results"]
        ],
    }


# ── Store Item Count Scraper ───────────────────────────────────────────────────
import shutil

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "store_results.json")


def _load_results() -> list[dict]:
    """Load store results from JSON file, return empty list if missing/corrupt."""
    if not os.path.exists(RESULTS_FILE):
        return []
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_results(results: list[dict]) -> bool:
    """Atomically write results to JSON, keeping a .bak of the previous version."""
    tmp = RESULTS_FILE + ".tmp"
    bak = RESULTS_FILE + ".bak"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        if os.path.exists(RESULTS_FILE):
            shutil.copy2(RESULTS_FILE, bak)
        os.replace(tmp, RESULTS_FILE)
        return True
    except OSError as e:
        print(f"⚠️  _save_results failed: {e}")
        return False


# ── Store Item Count Scraper ───────────────────────────────────────────────────

_store_scrape_jobs: dict[str, dict] = {}


@app.post("/scrape-stores-by-range", status_code=202)
def scrape_stores_by_range(
    request: StoreScrapeByRangeRequest,
    background_tasks: BackgroundTasks,
):
    """
    Starts a background store-count scrape job. Returns job_id immediately.

    Behaviour:
    - By default SKIPS store IDs already in store_results.json (append-only).
    - Pass "force_rescrape": true to re-scrape and overwrite existing entries.
    - Results are written atomically after every single store.
    - A .bak file is kept so a crash never wipes previous data.

    Poll endpoints:
    - GET /scrape-stores-by-range/{job_id}/summary  ← lightweight, use for polling
    - GET /scrape-stores-by-range/{job_id}          ← full results array

    Request body:
        {
            "row_range": "1-500",
            "force_rescrape": false    // optional, default false
        }
    """
    range_str = request.row_range.strip()
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", range_str)
    if not m:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid row_range '{range_str}'. Expected format: '1-20' or '40-500'.",
        )

    row_start = int(m.group(1))
    row_end   = int(m.group(2))

    if row_start < 1:
        raise HTTPException(status_code=422, detail="row_range start must be ≥ 1.")
    if row_end < row_start:
        raise HTTPException(status_code=422, detail="row_range end must be ≥ start.")

    csv_path = os.path.join(os.path.dirname(__file__), "stores_info_1_fixed.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail=f"CSV not found: {csv_path}")

    all_ids = load_store_ids_from_csv(csv_path)
    if not all_ids:
        raise HTTPException(status_code=400, detail="No store IDs found in CSV.")

    selected_ids = all_ids[row_start - 1:row_end]
    if not selected_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Range {row_start}-{row_end} produced no IDs. CSV has {len(all_ids)} rows.",
        )

    force_rescrape = getattr(request, "force_rescrape", False)
    existing       = _load_results()
    already_done   = set() if force_rescrape else {str(r["store_id"]) for r in existing}

    pending_ids = [sid for sid in selected_ids if sid not in already_done]
    skipped     = len(selected_ids) - len(pending_ids)

    job_id = str(uuid.uuid4())
    _store_scrape_jobs[job_id] = {
        "job_id":         job_id,
        "status":         "running",
        "row_range":      range_str,
        "total_ids":      len(selected_ids),
        "pending_ids":    len(pending_ids),
        "skipped":        skipped,
        "store_ids":      selected_ids,
        "started_at":     datetime.utcnow().isoformat(),
        "finished_at":    None,
        "completed":      0,
        "results_file":   RESULTS_FILE,
        "force_rescrape": force_rescrape,
        "error":          None,
    }

    def _run(job_id: str, pending: list[str], force: bool):
        try:
            results    = _load_results()
            already    = set() if force else {str(r["store_id"]) for r in results}
            idx_by_sid = {str(r["store_id"]): i for i, r in enumerate(results)}

            for sid in pending:
                if sid in already and not force:
                    _store_scrape_jobs[job_id]["completed"] += 1
                    continue

                try:
                    result = scrape_store_item_count(sid)
                except Exception as e:
                    result = {
                        "store_id":        sid,
                        "url":             None,
                        "item_count_text": None,
                        "item_count":      None,
                        "error":           str(e),
                        "source":          "exception",
                    }

                result["scraped_at"] = datetime.utcnow().isoformat()

                if force and sid in idx_by_sid:
                    results[idx_by_sid[sid]] = result
                else:
                    idx_by_sid[sid] = len(results)
                    results.append(result)

                saved = _save_results(results)
                print(
                    f"   {'💾 Saved' if saved else '⚠️  Save failed'} "
                    f"({len(results)} total) → store {sid}"
                )

                _store_scrape_jobs[job_id]["completed"] += 1

            _store_scrape_jobs[job_id]["status"]      = "completed"
            _store_scrape_jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()

        except Exception as e:
            _store_scrape_jobs[job_id]["status"]      = "failed"
            _store_scrape_jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
            _store_scrape_jobs[job_id]["error"]       = str(e)
            print(f"❌ Job {job_id[:8]} failed: {e}")

    background_tasks.add_task(_run, job_id, pending_ids, force_rescrape)

    print(
        f"\n📋 Job {job_id[:8]} started: rows {row_start}-{row_end} "
        f"→ {len(pending_ids)} to scrape, {skipped} already done (skipped)"
    )

    return {
        "job_id":         job_id,
        "status":         "running",
        "row_range":      range_str,
        "total_ids":      len(selected_ids),
        "pending_ids":    len(pending_ids),
        "skipped":        skipped,
        "force_rescrape": force_rescrape,
        "results_file":   RESULTS_FILE,
        "message":        f"Poll /scrape-stores-by-range/{job_id}/summary for progress.",
    }


@app.get("/scrape-stores-by-range/{job_id}/summary")
def get_store_scrape_summary(job_id: str):
    """
    Lightweight progress check — just numbers, no results array.
    Use this for polling while the job is running.
    """
    job = _store_scrape_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    pending   = job["pending_ids"]
    completed = job["completed"]

    return {
        "job_id":        job_id,
        "status":        job["status"],
        "row_range":     job["row_range"],
        "total_ids":     job["total_ids"],
        "skipped":       job["skipped"],
        "pending_ids":   pending,
        "completed":     completed,
        "remaining":     max(0, pending - completed),
        "progress_pct":  round((completed / pending) * 100, 1) if pending else 100.0,
        "started_at":    job["started_at"],
        "finished_at":   job["finished_at"],
        "error":         job["error"],
        "results_file":  job["results_file"],
    }


@app.get("/scrape-stores-by-range/{job_id}")
def get_store_scrape_job(job_id: str):
    """
    Full job status including all results from store_results.json.
    For polling use /summary instead — this loads the full file every call.
    """
    job = _store_scrape_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    pending   = job["pending_ids"]
    completed = job["completed"]
    results   = _load_results()

    return {
        "job_id":        job_id,
        "status":        job["status"],
        "row_range":     job["row_range"],
        "total_ids":     job["total_ids"],
        "skipped":       job["skipped"],
        "pending_ids":   pending,
        "completed":     completed,
        "remaining":     max(0, pending - completed),
        "progress_pct":  round((completed / pending) * 100, 1) if pending else 100.0,
        "started_at":    job["started_at"],
        "finished_at":   job["finished_at"],
        "error":         job["error"],
        "results_file":  job["results_file"],
        "total_in_file": len(results),
        "results":       results,
    }


@app.get("/store-results/summary")
def store_results_summary():
    """
    Quick summary of store_results.json without returning the full data.
    Shows total count broken down by source and error status.
    """
    results = _load_results()
    if not results:
        return {"total": 0, "breakdown": {}, "results_file": RESULTS_FILE}

    breakdown: dict[str, int] = {}
    errors = 0
    for r in results:
        src = r.get("source", "unknown")
        breakdown[src] = breakdown.get(src, 0) + 1
        if r.get("error"):
            errors += 1

    return {
        "total":        len(results),
        "with_errors":  errors,
        "successful":   len(results) - errors,
        "breakdown":    breakdown,
        "results_file": RESULTS_FILE,
    }
