from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Boolean, UniqueConstraint, Text, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)  # 로그인 ID
    password = Column(String(255), nullable=False)  # 비밀번호 (해시)
    nickname = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 🚀 [수정] 1:1 구조에서 1:N(유저가 여러 도메인의 설정을 보유) 구조로 전면 확장
    settings = relationship("UserSetting", back_populates="user", cascade="all, delete-orphan")
    
    # 유저가 보유한 다중 사이트 목록 (1:N)
    sites = relationship("Site", back_populates="owner", cascade="all, delete-orphan")


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    site_name = Column(String(100), nullable=False)
    domain = Column(String(255), index=True, nullable=False)  # 🚀 글로벌 unique=True 제거 (복합 유니크로 대체)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # 관계 설정
    owner = relationship("User", back_populates="sites")
    structures = relationship("BoardStructure", back_populates="site", cascade="all, delete-orphan")
    
    # 🚀 [핵심 추가] 이 사이트(도메인)가 가지는 전용 필터링 정책 설정 (Site : UserSetting = 1:1)
    setting = relationship("UserSetting", back_populates="site", uselist=False, cascade="all, delete-orphan")

    # 🚀 [핵심 추가] 동일한 사용자가 한 도메인을 중복 등록하는 것은 차단하되, 다른 사용자가 같은 도메인을 쓰는 것은 허용
    __table_args__ = (
        UniqueConstraint('user_id', 'domain', name='_user_domain_uc'),
    )


class BoardStructure(Base):
    __tablename__ = "board_structures"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    mid = Column(String(100))
    url_pattern = Column(String(500))
    get_selectors = Column(JSON)      # 분석된 GET 셀렉터 정보
    post_metadata = Column(JSON)      # 캡처된 POST 패킷 정보
    is_verified = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 역참조 관계 매핑
    site = relationship("Site", back_populates="structures")


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    
    # 🚀 [수정] 유저 ID 컬럼에서 unique=True 제약조건 제거 (도메인별로 여러 설정을 가져야 하므로)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # 🚀 [핵심 추가] 어떤 사이트(도메인)의 필터링 정책 환경설정인지 명시 (하나의 사이트는 단 하나의 설정을 가짐)
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    preferences = Column(JSON, nullable=False, default=dict) 
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 역참조 관계 매핑
    user = relationship("User", back_populates="settings")
    site = relationship("Site", back_populates="setting")


class DetectionLog(Base):
    __tablename__ = "detection_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    stage = Column(String(50))  # trie, bert 등
    reason = Column(String(255))
    
    # 🚀 대문자 Text로 교정하여 SQLAlchemy가 거대한 텍스트 필드로 인식하도록 합니다.
    blocked_content = Column(Text, nullable=True)  
    
    # 🚀 default 값은 datetime.utcnow (함수 호출 () 제외)로 매핑해야 데이터가 들어올 때 현재 시간이 찍힙니다.
    created_at = Column(DateTime, default=func.now())