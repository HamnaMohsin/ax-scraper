from pydantic import BaseModel
from typing import Optional, List


class ScrapeRequest(BaseModel):
    urls: str  # comma-separated AliExpress URLs, e.g. "https://.../item/123.html, https://.../item/456.html"

class CategoryRequest(BaseModel):
    title: str
    description: str


class ScrapeResult(BaseModel):
    url:                     str
    success:                 bool
    error:                   Optional[str] = None
    product_id:              Optional[int] = None
    original_title:          Optional[str] = None
    original_description:    Optional[str] = None
    enhanced_title:          Optional[str] = None
    enhanced_description:    Optional[str] = None
    llm_predicted_category:  Optional[str] = None
    assigned_category:       Optional[str] = None
    category_id:             Optional[int] = None
    similarity_score:        Optional[float] = None
    images:                  Optional[List[str]] = []


class ScrapeResponse(BaseModel):
    total:   int
    success: int
    failed:  int
    results: List[ScrapeResult]


class CategoryResponse(BaseModel):
    category_id:      int
    category_path:    str
    similarity_score: float


class ProductFetchedOut(BaseModel):
    product_id:  int
    url:         str
    title:       Optional[str]
    description: Optional[str]
    images:      Optional[List[str]] = []

    class Config:
        from_attributes = True


class ProductRefinedOut(BaseModel):
    id:                   int
    product_id:           int
    enhanced_title:       Optional[str]
    enhanced_description: Optional[str]

    class Config:
        from_attributes = True


class CategoryAssignmentOut(BaseModel):
    id:                     int
    product_id:             int
    llm_predicted_category: Optional[str]
    assigned_category:      Optional[str]
    category_id:            Optional[int]
    similarity_score:       Optional[float]

    class Config:
        from_attributes = True


class ProductFullOut(BaseModel):
    product_id:              int
    url:                     str
    title:                   Optional[str]
    description:             Optional[str]
    images:                  Optional[List[str]] = []
    enhanced_title:          Optional[str]
    enhanced_description:    Optional[str]
    llm_predicted_category:  Optional[str]
    assigned_category:       Optional[str]
    category_id:             Optional[int]
    similarity_score:        Optional[float]
