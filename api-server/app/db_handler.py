from sqlalchemy.orm import Session
from .models import Site, BoardStructure # api-server의 모델 참조

def save_analysis_results(db: Session, domain: str, analysis_data: dict):
    """분석된 구조 데이터를 DB에 영구 저장"""
    # 1. 사이트 레코드 가져오기 또는 생성
    site = db.query(Site).filter(Site.domain == domain).first()
    if not site:
        site = Site(domain=domain, site_name=domain.split('.')[0])
        db.add(site)
        db.commit()
        db.refresh(site)

    # 2. 게시판별 구조 저장
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
                get_selectors=info.get("get_selectors"), 
                post_metadata=info.get("post_metadata"),
                is_verified=True if info.get("post_metadata") else False
            )
            db.add(board)
        else:
            board.get_selectors = info.get("get_selectors")
            board.post_metadata = info.get("post_metadata")
            
    db.commit()