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

from data.export_to_template import load_products, write_category_file

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "data", "pdt_template_fr-FR_20260305_090255.xlsm")
DB_PATH       = os.path.join(BASE_DIR, "data","products.db")
OUT_DIR       = os.path.join(BASE_DIR, "data", "output_templates")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def extract_product_id(url: str) -> int:
    """
    Extract the numeric product ID from an AliExpress URL.
    e.g. https://www.aliexpress.com/item/1005006361896886.html → 1005006361896886
    """
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
        llm_predicted_category=p.category.llm_predicted_category if p.category else None,
        assigned_category=p.category.assigned_category if p.category else None,
        #category_id=p.category.category_id if p.category else None,
        category_id = str(p.category.category_id) if p.category and p.category.category_id is not None else None,
        similarity_score=p.category.similarity_score if p.category else None,
        exported_at=p.exported_at.isoformat() if p.exported_at else None, 
    )


def _upsert(db: Session, url: str, product_id: int, data: dict, refined: dict, category: dict) -> ProductFetched:
    """Insert or update all three tables atomically."""

    # 1. product_fetched
    product = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()
    if product:
        print("Product exists → updating")
        product.title       = data["title"]
        product.description = data["description_text"]
        product.images      = data["images"]
        product.exported_at = None
    else:
        print("New product → inserting")
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

    # 2. product_refined
    refined_row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()
    if refined_row:
        refined_row.enhanced_title       = refined["refined_title"]
        refined_row.enhanced_description = refined["refined_description"]
    else:
        db.add(ProductRefined(
            product_id=product_id,
            enhanced_title=refined["refined_title"],
            enhanced_description=refined["refined_description"],
        ))

    # 3. category_assignment
    cat_row = db.query(CategoryAssignment).filter(CategoryAssignment.product_id == product_id).first()
    if cat_row:
        cat_row.llm_predicted_category = category["llm_predicted_category"]
        cat_row.assigned_category      = category["matched_category_path"]
        cat_row.category_id            = category["matched_category_id"]
        cat_row.similarity_score       = float(category["similarity_score"])
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


# ─── Startup ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    print("Database tables created ✅")
    run_migrations()
    yield
    print("App shutting down...")


app = FastAPI(title="AliExpress Scraper API", lifespan=lifespan)


# ─── POST /scrape ─────────────────────────────────────────────────────────────

def _scrape_single(url: str) -> ScrapeResult:
    """Full scrape pipeline for one URL. Runs inside a thread."""
    url = url.strip()
    try:
        product_id = extract_product_id(url)
    except ValueError as e:
        return ScrapeResult(url=url, success=False, error=str(e))

    try:
        data = extract_aliexpress_product(url)

        if not data["title"] and not data["description_text"]:
            return ScrapeResult(url=url, success=False, error="No content extracted — page blocked or invalid")

        refined = refine_with_llm(data["title"], data["description_text"])
        if not data["description_text"]:
            refined["refined_description"] = ""

        category = categorize_product(
            title=refined["refined_title"],
            description=refined["refined_description"],
        )

        # Each thread gets its own DB session to avoid conflicts
        from database import SessionLocal
        db = SessionLocal()
        try:
            _upsert(db, url, product_id, data, refined, category)
        finally:
            db.close()

        print(f"✅ Saved product {product_id}")
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
        print(f"❌ Error scraping {url}: {e}")
        return ScrapeResult(url=url, success=False, error=str(e))


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest):
    """
    Accept comma-separated AliExpress URLs and scrape them all concurrently.
    Example: { "urls": "https://.../item/123.html, https://.../item/456.html" }
    """
    urls = [u.strip() for u in request.urls.split(",") if u.strip()]
    if not urls:
        raise HTTPException(status_code=422, detail="No valid URLs provided")

    results: List[ScrapeResult] = []

    # Run all URLs in parallel threads (max 5 at a time to avoid overloading Tor)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_scrape_single, url): url for url in urls}
        for future in as_completed(futures):
            results.append(future.result())

    succeeded = [r for r in results if r.success]
    failed    = [r for r in results if not r.success]

    return ScrapeResponse(
        total=len(results),
        success=len(succeeded),
        failed=len(failed),
        results=results,
    )


# ─── POST /categorize ────────────────────────────────────────────────────────

@app.post("/categorize", response_model=CategoryResponse)
async def categorize(request: CategoryRequest):
    try:
        category = await run_in_threadpool(
            categorize_product,
            title=request.title,
            description=request.description,
        )
        return CategoryResponse(
            category_id=category["matched_category_id"],
            category_path=category["matched_category_path"],
            similarity_score=float(category["similarity_score"]),
        )
    except Exception as e:
        print("Categorize error:", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─── GET /products ────────────────────────────────────────────────────────────

@app.get("/products", response_model=List[ProductFullOut])
def list_products(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Full joined view of all three tables. Supports pagination."""
    products = db.query(ProductFetched).offset(offset).limit(limit).all()
    return [_build_full_out(p) for p in products]




# ─── POST /scrape-only ────────────────────────────────────────────────────────
# Step 1: Scrape URL and save raw data to product_fetched only

@app.post("/scrape-only")
async def scrape_only(request: ScrapeRequest):
    """
    Scrape one or more URLs and save raw title, description, images
    to product_fetched table only. No LLM refinement or categorization.
    """
    urls = [u.strip() for u in request.urls.split(",") if u.strip()]
    if not urls:
        raise HTTPException(status_code=422, detail="No valid URLs provided")

    results = []

    def _scrape_and_save(url: str):
        try:
            product_id = extract_product_id(url)
        except ValueError as e:
            return {"url": url, "success": False, "error": str(e)}

        try:
            data = extract_aliexpress_product(url)
            if not data["title"]:
                return {"url": url, "success": False, "error": "No title extracted — page blocked or invalid"}

            from database import SessionLocal
            db = SessionLocal()
            try:
                product = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()
                if product:
                    product.title       = data["title"]
                    product.description = data["description_text"]
                    product.images      = data["images"]
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
        "total":   len(results),
        "success": sum(1 for r in results if r["success"]),
        "failed":  sum(1 for r in results if not r["success"]),
        "results": results,
    }


# ─── POST /refine/{product_id} ────────────────────────────────────────────────
# Step 2: Take raw title+description from product_fetched, refine with LLM,
#         save to product_refined

@app.post("/refine/{product_id}")
async def refine(product_id: int, db: Session = Depends(get_db)):
    """
    Read title and description from product_fetched for the given product_id,
    send to LLM for refinement, and save enhanced_title + enhanced_description
    to product_refined table.
    """
    product = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found in product_fetched")

    if not product.title:
        raise HTTPException(status_code=422, detail="Product has no title to refine")

    try:
        refined = await run_in_threadpool(
            refine_with_llm,
            product.title,
            product.description or "",
        )

        refined_row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()
        if refined_row:
            refined_row.enhanced_title       = refined["refined_title"]
            refined_row.enhanced_description = refined["refined_description"]
        else:
            refined_row = ProductRefined(
                product_id=product_id,
                enhanced_title=refined["refined_title"],
                enhanced_description=refined["refined_description"],
            )
            db.add(refined_row)

        db.commit()
        db.refresh(refined_row)

        return {
            "product_id":           product_id,
            "enhanced_title":       refined_row.enhanced_title,
            "enhanced_description": refined_row.enhanced_description,
        }

    except Exception as e:
        print(f"Refine error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── POST /assign-category/{product_id} ──────────────────────────────────────
# Step 3: Take enhanced_title+enhanced_description from product_refined,
#         assign category, save to category_assignment

@app.post("/assign-category/{product_id}")
async def assign_category(product_id: int, db: Session = Depends(get_db)):
    """
    Read enhanced_title and enhanced_description from product_refined
    for the given product_id, run embedding-based categorization,
    and save result to category_assignment table.
    """
    refined_row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()
    if not refined_row:
        raise HTTPException(status_code=404, detail="Product not found in product_refined — run /refine first")

    if not refined_row.enhanced_title:
        raise HTTPException(status_code=422, detail="No enhanced title found — run /refine first")

    try:
        category = await run_in_threadpool(
            categorize_product,
            title=refined_row.enhanced_title,
            description=refined_row.enhanced_description or "",
        )

        cat_row = db.query(CategoryAssignment).filter(CategoryAssignment.product_id == product_id).first()
        if cat_row:
            cat_row.llm_predicted_category = category["llm_predicted_category"]
            cat_row.assigned_category      = category["matched_category_path"]
            cat_row.category_id            = category["matched_category_id"]
            cat_row.similarity_score       = float(category["similarity_score"])
        else:
            cat_row = CategoryAssignment(
                product_id=product_id,
                llm_predicted_category=category["llm_predicted_category"],
                assigned_category=category["matched_category_path"],
                category_id=category["matched_category_id"],
                similarity_score=float(category["similarity_score"]),
            )
            db.add(cat_row)

        db.commit()
        db.refresh(cat_row)

        return {
            "product_id":             product_id,
            "llm_predicted_category": cat_row.llm_predicted_category,
            "assigned_category":      cat_row.assigned_category,
            "category_id":            cat_row.category_id,
            "similarity_score":       cat_row.similarity_score,
        }

    except Exception as e:
        print(f"Assign category error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/products/fetched", response_model=List[ProductFetchedOut])
def list_fetched(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List all raw scraped products. Supports pagination."""
    return db.query(ProductFetched).offset(offset).limit(limit).all()


@app.get("/products/fetched/{product_id}", response_model=ProductFetchedOut)
def get_fetched(product_id: int, db: Session = Depends(get_db)):
    """Get raw scraped data for a single product."""
    row = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in product_fetched")
    return row


@app.delete("/products/fetched/{product_id}")
def delete_fetched(product_id: int, db: Session = Depends(get_db)):
    """Delete a product and cascade to refined + category tables."""
    row = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in product_fetched")
    db.delete(row)
    db.commit()
    return {"message": f"Product {product_id} deleted (cascaded to all tables)"}


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 2 — product_refined
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/products/refined", response_model=List[ProductRefinedOut])
def list_refined(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List all LLM-enhanced products. Supports pagination."""
    return db.query(ProductRefined).offset(offset).limit(limit).all()


@app.get("/products/refined/{product_id}", response_model=ProductRefinedOut)
def get_refined(product_id: int, db: Session = Depends(get_db)):
    """Get LLM-enhanced title and description for a single product."""
    row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in product_refined")
    return row


# @app.delete("/products/refined/{product_id}")
# def delete_refined(product_id: int, db: Session = Depends(get_db)):
#     """Delete only the refined row for a product (keeps fetched + category)."""
#     row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()
#     if not row:
#         raise HTTPException(status_code=404, detail="Product not found in product_refined")
#     db.delete(row)
#     db.commit()
#     return {"message": f"Refined data for product {product_id} deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 3 — category_assignment
# ══════════════════════════════════════════════════════════════════════════════

# @app.get("/products/categories", response_model=List[CategoryAssignmentOut])
# def list_categories(
#     category_id: Optional[int] = Query(None, description="Filter by assigned category ID"),
#     min_score: Optional[float] = Query(None, description="Minimum similarity score"),
#     limit: int = Query(20, ge=1, le=100),
#     offset: int = Query(0, ge=0),
#     db: Session = Depends(get_db),
# ):
#     """List category assignments. Filter by category_id or min_score. Supports pagination."""
#     q = db.query(CategoryAssignment)
#     if category_id is not None:
#         q = q.filter(CategoryAssignment.category_id == category_id)
#     if min_score is not None:
#         q = q.filter(CategoryAssignment.similarity_score >= min_score)
#     return q.offset(offset).limit(limit).all()


# @app.get("/products/categories/{product_id}", response_model=CategoryAssignmentOut)
# def get_category(product_id: int, db: Session = Depends(get_db)):
#     """Get category assignment for a single product."""
#     row = db.query(CategoryAssignment).filter(CategoryAssignment.product_id == product_id).first()
#     if not row:
#         raise HTTPException(status_code=404, detail="Product not found in category_assignment")
#     return row


@app.delete("/products/categories/{product_id}")
def delete_category(product_id: int, db: Session = Depends(get_db)):
    """Delete only the category row for a product (keeps fetched + refined)."""
    row = db.query(CategoryAssignment).filter(CategoryAssignment.product_id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in category_assignment")
    db.delete(row)
    db.commit()
    return {"message": f"Category assignment for product {product_id} deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# JOINED VIEW — all three tables
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/products/{product_id}", response_model=ProductFullOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    """Full joined detail for a single product across all three tables."""
    p = db.query(ProductFetched).filter(ProductFetched.product_id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return _build_full_out(p)



@app.post("/export-templates")
async def export_templates(
    only_new: bool = Query(
        default=False,
        description="False = full rebuild. True = only export products where exported_at IS NULL, append to existing files."
    ),
    db: Session = Depends(get_db),
):
    if not os.path.exists(TEMPLATE_PATH):
        raise HTTPException(status_code=500, detail=f"Template not found: {TEMPLATE_PATH}")
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=500, detail=f"Database not found: {DB_PATH}")

    def _run_export():
        os.makedirs(OUT_DIR, exist_ok=True)

        df = load_products(DB_PATH, only_new=only_new)
        if df.empty:
            msg = "No new products to export." if only_new else "No categorized products found. Run /scrape first."
            return {"mode": "incremental" if only_new else "full", "total_products": 0, "total_categories": 0, "output_dir": OUT_DIR, "message": msg, "files": [], "_written_ids": []}

        sample_images = df.iloc[0].get("images")
        print(f"DEBUG images type={type(sample_images).__name__} | sample={str(sample_images)[:80]}")

        all_written_ids = []
        results = []

        for category_id, group in df.groupby("category_id"):
            category_name = group.iloc[0]["assigned_category"] or str(category_id)
            safe_filename = str(category_id).replace("/", "_").replace(" ", "_")
            out_path      = os.path.join(OUT_DIR, f"{safe_filename}.xlsm")

            print(f"Category [{category_id}] {category_name} — {len(group)} product(s)")

            written_ids = write_category_file(
                template_path=TEMPLATE_PATH,
                out_path=out_path,
                category_code=str(category_id),
                category_name=str(category_name),
                products=group,
                append=only_new,
            )
            all_written_ids.extend(written_ids)

            product_summaries = []
            for _, row in group.iterrows():
                raw_images = row.get("images")
                image_count = 0
                if raw_images is not None:
                    if isinstance(raw_images, list):
                        image_count = len(raw_images)
                    elif isinstance(raw_images, str) and raw_images.strip() not in ("", "null", "[]"):
                        try:
                            image_count = len(json.loads(raw_images))
                        except Exception:
                            image_count = 0
                print(f"  product {row['product_id']} | images type={type(raw_images).__name__} | count={image_count}")

                product_summaries.append({
                    "product_id":      str(row["product_id"]),
                    "title":           row.get("enhanced_title") or row.get("original_title") or "",
                    "image_count":     image_count,
                    "has_description": bool(row.get("enhanced_description") or row.get("original_description")),
                })

            results.append({
                "category_id":   str(category_id),
                "category_name": category_name,
                "product_count": len(group),
                "file":          out_path,
                "products":      product_summaries,
            })

        return {
            "mode":             "incremental" if only_new else "full",
            "total_products":   len(all_written_ids),
            "total_categories": len(results),
            "output_dir":       OUT_DIR,
            "files":            results,
            "_written_ids":     all_written_ids,
        }

    try:
        result = await run_in_threadpool(_run_export)

        written_ids = result.pop("_written_ids", [])
        if written_ids:
            now = datetime.now(timezone.utc)
            db.query(ProductFetched).filter(ProductFetched.product_id.in_(written_ids)).update(
                {"exported_at": now}, synchronize_session=False
            )
            db.commit()
            print(f"Marked {len(written_ids)} product(s) as exported at {now.isoformat()}")

        print(f"Export complete [{result['mode']}] — {result['total_products']} product(s) across {result['total_categories']} file(s)")
        return result

    except Exception as e:
        print(f"Export error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

