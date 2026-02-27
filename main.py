import re
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import ProductFetched, ProductRefined, CategoryAssignment
from schemas import (
    ScrapeRequest, ScrapeResponse, ScrapeResult,
    CategoryRequest, CategoryResponse,
    ProductFetchedOut, ProductRefinedOut,
    CategoryAssignmentOut, ProductFullOut,
)
from scraper import extract_aliexpress_product
from llm_refiner import refine_with_llm
from assign_embeddings import categorize_product


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
        category_id=p.category.category_id if p.category else None,
        similarity_score=p.category.similarity_score if p.category else None,
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
    else:
        print("New product → inserting")
        product = ProductFetched(
            product_id=product_id,
            url=url,
            title=data["title"],
            description=data["description_text"],
            images=data["images"],
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


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 1 — product_fetched
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


@app.delete("/products/refined/{product_id}")
def delete_refined(product_id: int, db: Session = Depends(get_db)):
    """Delete only the refined row for a product (keeps fetched + category)."""
    row = db.query(ProductRefined).filter(ProductRefined.product_id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in product_refined")
    db.delete(row)
    db.commit()
    return {"message": f"Refined data for product {product_id} deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 3 — category_assignment
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/products/categories", response_model=List[CategoryAssignmentOut])
def list_categories(
    category_id: Optional[int] = Query(None, description="Filter by assigned category ID"),
    min_score: Optional[float] = Query(None, description="Minimum similarity score"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List category assignments. Filter by category_id or min_score. Supports pagination."""
    q = db.query(CategoryAssignment)
    if category_id is not None:
        q = q.filter(CategoryAssignment.category_id == category_id)
    if min_score is not None:
        q = q.filter(CategoryAssignment.similarity_score >= min_score)
    return q.offset(offset).limit(limit).all()


@app.get("/products/categories/{product_id}", response_model=CategoryAssignmentOut)
def get_category(product_id: int, db: Session = Depends(get_db)):
    """Get category assignment for a single product."""
    row = db.query(CategoryAssignment).filter(CategoryAssignment.product_id == product_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found in category_assignment")
    return row


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
