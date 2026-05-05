from sqlalchemy import Column, Integer, String, JSON, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class Site(Base):
    """최상위 도메인 및 사이트 정보"""
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(255), unique=True, index=True) # 예: zeropage.org
    site_name = Column(String(100))
    platform_type = Column(String(50)) # 예: Rhymix, XE, Gnuboard (패턴 파악용)
    
    # 생성 및 수정 시간
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # 관계 설정: 하나의 사이트는 여러 게시판 구조를 가짐
    boards = relationship("BoardStructure", back_populates="site")

class BoardStructure(Base):
    """게시판(mid)별 상세 수집/전송 규칙"""
    __tablename__ = "board_structures"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"))
    
    # 식별 정보
    mid = Column(String(100), index=True) # 게시판 고유 ID (notice, board 등)
    url_pattern = Column(String(500)) # 분석 시 사용된 대표 URL
    
    # GET 규칙 (UGC 수집용 셀렉터)
    # { "article": "#read_obj", "comment": ".comment_list", "title": ".title_class" }
    get_selectors = Column(JSON)
    
    # POST 규칙 (패킷 스니핑 결과)
    # { "endpoint": "/index.php", "params": ["mid", "content", "title", "act"], "act_key": "procBoardInsert" }
    post_metadata = Column(JSON)
    
    is_verified = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    site = relationship("Site", back_populates="boards")