from models import Site, BoardStructure
from sqlalchemy.orm import Session

def save_analysis_results(db: Session, domain: str, analysis_data: dict):
    """분석된 구조 데이터를 RDS에 영구 저장 (Debug 포함)"""
    print(f"[DB] 💾 {domain} 데이터를 DB에 저장 시도 중...")
    
    # 1. 사이트 레코드 가져오기 또는 생성
    site = db.query(Site).filter(Site.domain == domain).first()
    if not site:
        site = Site(domain=domain, site_name=domain.split('.')[0])
        db.add(site)
        db.commit()
        db.refresh(site)
        print(f"[DB]   └─ 신규 사이트 등록 완료 (ID: {site.id})")

    # 2. 게시판별 구조 저장 (mid 기준)
    for mid, info in analysis_data.items():
        board = db.query(BoardStructure).filter(
            BoardStructure.site_id == site.id, 
            BoardStructure.mid == mid
        ).first()

        if not board:
            board = BoardStructure(
                site_id=site.id,
                mid=mid,
                url_pattern=info.get("url_pattern"),
                # 리스트 형태의 텍스트 데이터가 그대로 JSON 컬럼에 들어감
                get_selectors=info.get("get_selectors"), 
                post_metadata=info.get("post_metadata"), # 패킷 정보는 아까 방식 유지
                is_verified=True if info.get("post_metadata") else False
            )
            db.add(board)
        else:
            board.get_selectors = info.get("get_selectors")
            board.post_metadata = info.get("post_metadata")
            
    db.commit()
    print("[DB] ✅ 모든 데이터가 안전하게 RDS에 반영되었습니다.")