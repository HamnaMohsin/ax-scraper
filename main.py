from schemas import ScrapeRequest, ScrapeResponse,CategoryRequest,CategoryResponse
from scraper import extract_aliexpress_product
from llm_refiner import refine_with_llm
from fastapi import FastAPI, HTTPException,Depends
from fastapi.concurrency import run_in_threadpool
from assign_embeddings import categorize_product
from contextlib import asynccontextmanager
from database import Base, engine, get_db
from sqlalchemy.orm import Session
from models import Product
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to run on startup
    Base.metadata.create_all(bind=engine)
    print("Database tables created ✅")
    yield
    # Code to run on shutdown (optional)
    print("App shutting down...")

app = FastAPI(title="AliExpress Scraper API",lifespan=lifespan)


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest, db: Session = Depends(get_db)):
    try:
        print("Starting scrape...")

        data = await run_in_threadpool(
            extract_aliexpress_product, request.url
        )
        

        if not data["title"] and not data["description_text"]:
            raise HTTPException(
                status_code=422,
                detail="Failed to extract product content. Page may be invalid,blocked or dynamic."
            )

        

        refined = refine_with_llm(
            data["title"],
            data["description_text"]
        )
        if data["description_text"]=="":
            refined["refined_description"]=" " #if no description was found then return text empty
            
        
        category=categorize_product(
    title=refined["refined_title"],
    description=refined["refined_description"]
       )
        print(category)
        # Save to SQLite
         # -----------------------------
         
        existing_product = (
            db.query(Product)
            .filter(Product.url == request.url)
            .first()
        )

        if existing_product:
            print("Product already exists → updating")

            existing_product.title = data["title"]
            existing_product.description = data["description_text"]
            existing_product.refined_title = refined["refined_title"]
            existing_product.refined_description = refined["refined_description"]
            existing_product.category_id = category["matched_category_id"]
            existing_product.category_path = category["matched_category_path"]
            existing_product.similarity_score = float(category["similarity_score"])
            existing_product.llm_predicted_path = category["llm_predicted_category"]
            existing_product.images = data["images"] 
            db.commit()
            db.refresh(existing_product)

            db_product = existing_product

        else:
            print("New product → inserting")

            db_product = Product(
                url=request.url,
                title=data["title"],
                description=data["description_text"],
                refined_title=refined["refined_title"],
                refined_description=refined["refined_description"],
                category_id=category["matched_category_id"],
                category_path=category["matched_category_path"],
                similarity_score=float(category["similarity_score"]),
                llm_predicted_path=category["llm_predicted_category"],
                images=data["images"] 
            )

            db.add(db_product)
            db.commit()
            db.refresh(db_product)

        print(f"Product saved with ID {db_product.id}")
        
        return ScrapeResponse(
            original_title=data["title"],
            original_description=data["description_text"],
            refined_title=refined["refined_title"],
            refined_description=refined["refined_description"],
            category_id=category["matched_category_id"],
            category_path=category["matched_category_path"],
            llm_predicted_path=category["llm_predicted_category"],
            similarity_score=category["similarity_score"],
            images=data["images"] 
        )

    except HTTPException:
        # IMPORTANT: re-raise HTTPException unchanged
        raise

    except Exception as e:
        print("Unexpected server error:", e)
        raise HTTPException(status_code=500, detail="Internal server error")




@app.get("/products")
def list_products(db: Session = Depends(get_db)):
    products = db.query(Product).all()

    return [
        {
            "id": p.id,
            "url": p.url,
            "title": p.title,
            "category_id": p.category_id,
            "category_path": p.category_path,
            "llm_predicted_path": p.llm_predicted_path,
            "similarity_score": p.similarity_score,
            "description_length": len(p.description or ""),
            "images":p.images or []  
        }
        for p in products
       ]
