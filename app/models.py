from sqlalchemy import Column, Integer, String, JSON, DateTime, Boolean
from sqlalchemy.sql import func
from .database import Base

class TargetSite(Base):
    __tablename__ = "target_sites"

    id = Column(Integer, primary_key=True, index=True)
    url_pattern = Column(String(500), unique=True, index=True) 
    domain = Column(String(255), index=True)
    site_name = Column(String(100))
    
    dom_metadata = Column(JSON)
    http_metadata = Column(JSON)
    url_structure = Column(JSON)
    
    is_verified = Column(Boolean, default=False)
    
    # 생성 시 기본 시간 설정
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # 수정(Update) 시마다 현재 시간으로 자동 갱신 (onupdate 설정)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())