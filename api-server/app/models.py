from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from .database import Base

class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(255), unique=True, index=True)
    site_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class BoardStructure(Base):
    __tablename__ = "board_structures"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"))
    mid = Column(String(100))
    url_pattern = Column(String(500))
    get_selectors = Column(JSON)      # 분석된 GET 셀렉터 정보
    post_metadata = Column(JSON)      # 캡처된 POST 패킷 정보
    is_verified = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())