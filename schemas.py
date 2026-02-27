from pydantic import BaseModel
from typing import Optional, List


class ScrapeRequest(BaseModel):
    url: str

class CategoryRequest(BaseModel):
    title: str
    description: str


class ScrapeResponse(BaseModel):
    product_id:              int
    original_title:          str
    original_description:    Optional[str] = ""
    enhanced_title:          str
    enhanced_description:    Optional[str] = ""
    llm_predicted_category:  str
    assigned_category:       str
    category_id:             int
    similarity_score:        float
    images:                  Optional[List[str]] = []


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
