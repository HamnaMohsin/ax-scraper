from pydantic import BaseModel
from typing import Optional,List
class ScrapeRequest(BaseModel):
    url: str

class ScrapeResponse(BaseModel):
    original_title: str
    # original_description: str
    original_description: Optional[str] = ""  # default empty string
    refined_title: str
    refined_description: Optional[str] = ""
    category_id: int
    category_path: str
    similarity_score: float
    llm_predicted_path: str
    # images: list[str] 
    images: Optional[List[str]] = []  # default empty list
    

    
class CategoryRequest(BaseModel):
    title: str
    description: str


class CategoryResponse(BaseModel):
    category_id: int
    category_path: str
    similarity_score: float

    