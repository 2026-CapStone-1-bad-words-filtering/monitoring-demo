from pydantic import BaseModel
from typing import Dict, Any

class DetectRequest(BaseModel):
    content: str

class StageResult(BaseModel):
    is_inappropriate: bool
    label: str
    reason: str
    score: float = 0.0
    details: Dict[str, Any] = {}