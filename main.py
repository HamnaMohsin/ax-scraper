import re
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List
import os
import sys
import json
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from datetime import datetime, timezone
from database import Base, engine, get_db, run_migrations

from models import ProductFetched, ProductRefined, CategoryAssignment
from schemas import (
    ScrapeRequest, ScrapeResponse, ScrapeResult,
    CategoryRequest, CategoryResponse,
    ProductFetchedOut, ProductRefinedOut,
    CategoryAssignmentOut, ProductFullOut,
)
from scraper2 import extract_aliexpress_product
from llm_refiner2 import refine_with_llm
from assign_embeddings2 import categorize_product

from data.export_to_template import load_products, write_category_file, make_safe_filename


BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "data", "pdt_template_fr-FR_20260305_090255.xlsm")
DB_PATH       = os.path.join(BASE_DIR, "data","products.db")
OUT_DIR       = os.path.join(BASE_DIR, "data", "output_templates")


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def extract_product_id(url: str) -> int:
    match = re.search(r"/item/(\d+)", url)
    if not match:
        raise ValueError(f"Could not extract product ID from URL: {url}")
    return int(match.group(1))


def _build_full_out(p: ProductFetched) -> ProductFullOut:
    return ProductFullOut(
        product_id=p.product_id,
        url=p.url,
        title=p.title,
        description=p.description,
        images=p.images or [],
        enhanced_title=p.refined.enhanced_title if p.refined else None,
        enhanced_description=p.refined.enhanced_description if p.refined else None,
        description_marketing=p.refined.description_marketing if p.refined else None,
        llm_predicted_category=p.category.llm_predicted_category if p.category else None,
        assigned_category=p.category.assigned_category if p.category else None,
        category_id=str(p.category.category_id) if p.category and p.category.category_id is not None else None,
        similarity_score=p.category.similarity_score if p.category else None,
        exported_at=p.exported_at.isoformat() if p.exported_at else None,
    )


def _upsert(db: Session, url: str, product_id: int, data: dict, refined: dict, category: dict) -> ProductFetched:

    product = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()

    if product:
        product.title = data["title"]
        product.description = data["description_text"]
        product.images = data["images"]
        product.exported_at = None
    else:
        product = ProductFetched(
            product_id=product_id,
            url=url,
            title=data["title"],
            description=data["description_text"],
            images=data["images"],
            exported_at=None,
        )
        db.add(product)

    db.flush()

    refined_row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()

    if refined_row:
        refined_row.enhanced_title = refined["refined_title"]
        refined_row.enhanced_description = refined["refined_description"]
        refined_row.description_marketing = refined["description_marketing"]
    else:
        db.add(ProductRefined(
            product_id=product_id,
            enhanced_title=refined["refined_title"],
            enhanced_description=refined["refined_description"],
            description_marketing=refined["description_marketing"],
        ))

    cat_row = db.query(CategoryAssignment).filter(CategoryAssignment.product_id == product_id).first()

    if cat_row:
        cat_row.llm_predicted_category = category["llm_predicted_category"]
        cat_row.assigned_category = category["matched_category_path"]
        cat_row.category_id = category["matched_category_id"]
        cat_row.similarity_score = float(category["similarity_score"])
    else:
        db.add(CategoryAssignment(
            product_id=product_id,
            llm_predicted_category=category["llm_predicted_category"],
            assigned_category=category["matched_category_path"],
            category_id=category["matched_category_id"],
            similarity_score=float(category["similarity_score"]),
        ))

    db.commit()
    db.refresh(product)

    return product


# ─────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):

    Base.metadata.create_all(bind=engine)
    run_migrations()

    yield


app = FastAPI(title="AliExpress Scraper API", lifespan=lifespan)


# ─────────────────────────────────────────────────────────────
# SCRAPE
# ─────────────────────────────────────────────────────────────

def _scrape_single(url: str) -> ScrapeResult:

    url = url.strip()

    try:
        product_id = extract_product_id(url)
    except ValueError as e:
        return ScrapeResult(url=url, success=False, error=str(e))

    try:
        data = extract_aliexpress_product(url)

        if not data["title"] and not data["description_text"]:
            return ScrapeResult(url=url, success=False, error="No content extracted")

        refined = refine_with_llm(data["title"], data["description_text"])

        if not data["description_text"]:
            refined["refined_description"] = ""

        category = categorize_product(
            title=refined["refined_title"],
            description=refined["refined_description"],
        )

        from database import SessionLocal
        db = SessionLocal()

        try:
            _upsert(db, url, product_id, data, refined, category)
        finally:
            db.close()

        return ScrapeResult(
            url=url,
            success=True,
            product_id=product_id,
            original_title=data["title"],
            original_description=data["description_text"],
            enhanced_title=refined["refined_title"],
            enhanced_description=refined["refined_description"],
            llm_predicted_category=category["llm_predicted_category"],
            assigned_category=category["matched_category_path"],
            category_id=category["matched_category_id"],
            similarity_score=float(category["similarity_score"]),
            images=data["images"],
        )

    except Exception as e:
        return ScrapeResult(url=url, success=False, error=str(e))


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest):

    urls = [u.strip() for u in request.urls.split(",") if u.strip()]

    if not urls:
        raise HTTPException(status_code=422, detail="No URLs provided")

    results: List[ScrapeResult] = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_scrape_single, url): url for url in urls}

        for future in as_completed(futures):
            results.append(future.result())

    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    return ScrapeResponse(
        total=len(results),
        success=len(succeeded),
        failed=len(failed),
        results=results,
    )


# ─────────────────────────────────────────────────────────────
# SCRAPE ONLY
# ─────────────────────────────────────────────────────────────

@app.post("/scrape-only")
async def scrape_only(request: ScrapeRequest):

    urls = [u.strip() for u in request.urls.split(",") if u.strip()]

    if not urls:
        raise HTTPException(status_code=422, detail="No URLs provided")

    results = []

    def _scrape_and_save(url: str):

        try:
            product_id = extract_product_id(url)
        except ValueError as e:
            return {"url": url, "success": False, "error": str(e)}

        try:
            data = extract_aliexpress_product(url)

            from database import SessionLocal
            db = SessionLocal()

            try:

                product = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()

                if product:
                    product.title = data["title"]
                    product.description = data["description_text"]
                    product.images = data["images"]

                else:
                    product = ProductFetched(
                        product_id=product_id,
                        url=url,
                        title=data["title"],
                        description=data["description_text"],
                        images=data["images"],
                    )

                    db.add(product)

                db.commit()
                db.refresh(product)

            finally:
                db.close()

            return {
                "url": url,
                "success": True,
                "product_id": product_id,
                "title": data["title"],
                "description": data["description_text"],
                "images": data["images"],
            }

        except Exception as e:
            return {"url": url, "success": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=2) as executor:

        futures = {executor.submit(_scrape_and_save, url): url for url in urls}

        for future in as_completed(futures):
            results.append(future.result())

    return {
        "total": len(results),
        "success": sum(1 for r in results if r["success"]),
        "failed": sum(1 for r in results if not r["success"]),
        "results": results,
    }


# ─────────────────────────────────────────────────────────────
# REFINE
# ─────────────────────────────────────────────────────────────

@app.post("/refine/{product_id}")
async def refine(product_id: int, db: Session = Depends(get_db)):

    product = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    refined = await run_in_threadpool(
        refine_with_llm,
        product.title,
        product.description or "",
    )

    refined_row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()

    if refined_row:
        refined_row.enhanced_title = refined["refined_title"]
        refined_row.enhanced_description = refined["refined_description"]
        refined_row.description_marketing = refined["description_marketing"]

    else:
        refined_row = ProductRefined(
            product_id=product_id,
            enhanced_title=refined["refined_title"],
            enhanced_description=refined["refined_description"],
            description_marketing=refined["description_marketing"],
        )
        db.add(refined_row)

    db.commit()
    db.refresh(refined_row)

    return {
        "product_id": product_id,
        "enhanced_title": refined_row.enhanced_title,
        "enhanced_description": refined_row.enhanced_description,
    }
