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

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}
