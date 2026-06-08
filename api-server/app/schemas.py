from pydantic import BaseModel, Field
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

class UserPreferences(BaseModel):
    banned_types: List[str] = Field(default_factory=list)
    is_active: bool = True
    sensitivity: str = "medium"
    custom_keywords: List[str] = Field(default_factory=list)

class UpdateSettingRequest(BaseModel):
    user_id: int
    preferences: UserPreferences

class FilterProxyRequest(BaseModel):
    text: str
    user_id: int