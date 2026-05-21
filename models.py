from sqlalchemy import Column, BigInteger, Integer, String, Float, Text, JSON, ForeignKey, DateTime,Index
from sqlalchemy.orm import relationship
from database import Base
from sqlalchemy import UniqueConstraint

class ProductFetched(Base):
    __tablename__ = "product_fetched"

    product_id  = Column(BigInteger, primary_key=True, index=True)
    url         = Column(String, unique=True, index=True, nullable=False)
    title       = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    images      = Column(JSON, nullable=True)
    specifications = Column(JSON, nullable=True) 
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
    specifications        = Column(JSON, nullable=True)

    product = relationship("ProductFetched", back_populates="refined")
    translation = relationship("ProductTranslation", back_populates="product", uselist=False, cascade="all, delete-orphan")


class CategoryAssignment(Base):
    __tablename__ = "category_assignment"

    id                     = Column(Integer, primary_key=True, autoincrement=True)
    product_id             = Column(BigInteger, ForeignKey("product_fetched.product_id", ondelete="CASCADE"), unique=True, nullable=False)
    llm_predicted_category = Column(String, nullable=True)
    assigned_category      = Column(String, nullable=True)
    category_id            = Column(String, nullable=True)
    similarity_score       = Column(Float, nullable=True)

    product = relationship("ProductFetched", back_populates="category")

class ManufacturerInfo(Base):
    __tablename__ = "manufacturer_info"

    store_name = Column(String, primary_key=True, nullable=False)
    store_id   = Column(String, primary_key=True, nullable=False)
    name       = Column(String, nullable=True)
    address    = Column(Text, nullable=True)
    email      = Column(String, nullable=True)
    phone      = Column(String, nullable=True)
    

class ProductTranslation(Base):
    __tablename__ = "product_translations"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(BigInteger, ForeignKey("product_refined.product_id", ondelete="CASCADE"), unique=True, nullable=False)

    title_romanian              = Column(Text, nullable=True)
    description_romanian        = Column(Text, nullable=True)
    specifications_romanian     = Column(JSON, nullable=True)   # ← added

    title_german                = Column(Text, nullable=True)
    description_german          = Column(Text, nullable=True)
    specifications_german       = Column(JSON, nullable=True)   # ← added

    title_portuguese            = Column(Text, nullable=True)
    description_portuguese      = Column(Text, nullable=True)
    specifications_portuguese   = Column(JSON, nullable=True)   # ← added

    title_finnish               = Column(Text, nullable=True)
    description_finnish         = Column(Text, nullable=True)
    specifications_finnish      = Column(JSON, nullable=True)   # ← added

    title_french                = Column(Text, nullable=True)
    description_french          = Column(Text, nullable=True)
    specifications_french       = Column(JSON, nullable=True)   # ← added

    product = relationship("ProductRefined", back_populates="translation")


class ProductVariant(Base):
    """
    One row per variant type.

    Example:

        product_id      = 1001
        variant_type    = "Color"
        variant_values  = "black,white,blue"

        product_id      = 1001
        variant_type    = "Size"
        variant_values  = "S,M,L"
    """

    __tablename__ = "product_variant"

    id = Column(Integer, primary_key=True, index=True)

    product_id = Column(Integer, nullable=False, index=True)

    variant_type = Column(String(255), nullable=False, index=True)

    variant_values = Column(Text, nullable=False)

    scraped_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "ix_product_variant_unique",
            "product_id",
            "variant_type",
            unique=True,
        ),
    )

    def values_list(self) -> list[str]:
        return [
            v.strip()
            for v in (self.variant_values or "").split(",")
            if v.strip()
        ]
