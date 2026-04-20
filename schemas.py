from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


class ScrapeRequest(BaseModel):
    urls: str   # comma-separated AliExpress URLs


class CategorizeRequest(BaseModel):
    title: str
    description: str


# ── Single-table views ─────────────────────────────────────────────────────────

class ProductFetchedOut(BaseModel):
    product_id:  int
    url:         str
    title:       Optional[str] = None
    description: Optional[str] = None
    images:      Optional[list] = None
    exported_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class ProductRefinedOut(BaseModel):
    product_id:            int
    enhanced_title:        Optional[str] = None
    enhanced_description:  Optional[str] = None
    description_marketing: Optional[str] = None
    model_config = {"from_attributes": True}


class CategoryAssignmentOut(BaseModel):
    product_id:             int
    llm_predicted_category: Optional[str] = None
    assigned_category:      Optional[str] = None
    category_id:            Optional[str] = None
    similarity_score:       Optional[float] = None
    model_config = {"from_attributes": True, "coerce_numbers_to_str": True} 


# ✅ NEW: Standalone category response (for /categorize endpoint — no product_id)
class CategoryStandaloneOut(BaseModel):
    """Response schema for standalone /categorize endpoint."""
    llm_predicted_category: Optional[str] = None
    category_id:            Optional[str] = None
    category_path:          Optional[str] = None
    similarity_score:       Optional[float] = None
    model_config = {"from_attributes": True, "coerce_numbers_to_str": True} 


# ── Full joined view ───────────────────────────────────────────────────────────

class ProductFullOut(BaseModel):
    product_id:             int
    url:                    Optional[str] = None
    title:                  Optional[str] = None
    description:            Optional[str] = None
    images:                 Optional[list] = None
    exported_at:            Optional[datetime] = None
    # from product_refined
    enhanced_title:         Optional[str] = None
    enhanced_description:   Optional[str] = None
    description_marketing:  Optional[str] = None
    # from category_assignment
    llm_predicted_category: Optional[str] = None
    assigned_category:      Optional[str] = None
    category_id:            Optional[str] = None
    similarity_score:       Optional[float] = None
    model_config = {"from_attributes": True, "coerce_numbers_to_str": True} 

class ManufacturerInfoOut(BaseModel):
    store_name: str
    store_id:   str
    name:       Optional[str] = None
    address:    Optional[str] = None
    email:      Optional[str] = None
    phone:      Optional[str] = None

    model_config = {"from_attributes": True}
class ProductDetailsRequest(BaseModel):
    ids: List[int]
    output_file: str = "ax_products.json"
