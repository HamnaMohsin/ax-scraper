from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Product  # adjust import if needed
from database.py import DATABASE_URL,engine 
#DATABASE_URL = "sqlite:///./products.db"  # adjust path if different

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

products = db.query(Product).all()

print(f"\nTotal products: {len(products)}\n")

for p in products:
    print("-" * 80)
    print(f"ID: {p.id}")
    print(f"URL: {p.url}")
    print(f"Title: {p.title}")
    print(f"Category ID: {p.category_id}")
    print(f"Category Path: {p.category_path}")
    print(f"LLM Predicted Path: {p.llm_predicted_path}")
    print(f"Similarity Score: {p.similarity_score}")
    print(f"Description length: {len(p.description or '')}")
