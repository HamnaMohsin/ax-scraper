from sqlalchemy import Column, BigInteger, Integer, String, Float, Text, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base


class ProductFetched(Base):
    __tablename__ = "product_fetched"

    product_id  = Column(BigInteger, primary_key=True, index=True)
    url         = Column(String, unique=True, index=True, nullable=False)
    title       = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    images      = Column(JSON, nullable=True)
    exported_at = Column(DateTime, nullable=True)

    refined  = relationship("ProductRefined",     back_populates="product", uselist=False, cascade="all, delete-orphan")
    category = relationship("CategoryAssignment", back_populates="product", uselist=False, cascade="all, delete-orphan")


class ProductRefined(Base):
    __tablename__ = "product_refined"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    product_id            = Column(BigInteger, ForeignKey("product_fetched.product_id", ondelete="CASCADE"), unique=True, nullable=False)
    enhanced_title        = Column(String, nullable=True)
    enhanced_description  = Column(Text, nullable=True)
    description_marketing = Column(Text, nullable=True)   # HTML, max 5000 chars — LLM output

    product = relationship("ProductFetched", back_populates="refined")


class CategoryAssignment(Base):
    __tablename__ = "category_assignment"

    id                     = Column(Integer, primary_key=True, autoincrement=True)
    product_id             = Column(BigInteger, ForeignKey("product_fetched.product_id", ondelete="CASCADE"), unique=True, nullable=False)
    llm_predicted_category = Column(String, nullable=True)
    assigned_category      = Column(String, nullable=True)
    category_id            = Column(String, nullable=True)
    similarity_score       = Column(Float, nullable=True)

    product = relationship("ProductFetched", back_populates="category")
