# models.py
from sqlalchemy import Column, Integer, String, Float, Text,JSON 
from database import Base
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    refined_title = Column(String, nullable=True)
    refined_description = Column(Text, nullable=True)
    category_id = Column(Integer, nullable=True)
    category_path = Column(String, nullable=True)
    similarity_score = Column(Float, nullable=True)
    llm_predicted_path = Column(String, nullable=True)
    images = Column(JSON, nullable=True) 
