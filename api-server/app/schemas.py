from pydantic import BaseModel
from typing import List, Optional

class AnalyzeRequest(BaseModel):
    url: str

class FilterRequest(BaseModel):
    site_id: int
    content: str

class FilterResponse(BaseModel):
    is_filtered: bool
    action: str  # "mask", "block", "none" 등
    modified_content: Optional[str] = None