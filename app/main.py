import sys
import asyncio
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from .database import engine, Base, get_db
from . import models, analyzer

# DB 테이블 자동 생성
models.Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.post("/api/analyze")
async def start_analysis(url: str, db: Session = Depends(get_db)):
    worker = analyzer.SiteAnalyzer(url)
    
    # 1. 캡처 및 LLM 분석
    await worker.capture_all()
    analysis = await worker.analyze_with_llm()
    
    # 2. GET 검증
    verified_get = await worker.verify_analysis(analysis.get('get_rules', []))
    
    # 3. DB 처리
    pattern = analysis.get('url_pattern', url)
    domain = url.split('/')[2]
    
    existing = db.query(models.TargetSite).filter(models.TargetSite.url_pattern == pattern).first()
    
    data_payload = {
        "url_pattern": pattern,
        "domain": domain,
        "site_name": domain,
        "dom_metadata": {"get_rules": verified_get},
        "http_metadata": analysis.get('post_rules', {}),
        "url_structure": {"structural_params": analysis.get('structural_params', [])},
        "is_verified": True if verified_get else False
    }

    if existing:
        for key, value in data_payload.items():
            setattr(existing, key, value)
    else:
        new_site = models.TargetSite(**data_payload)
        db.add(new_site)
    
    db.commit()
    db.refresh(existing if existing else new_site)
    
    return {"status": "success", "data": existing if existing else new_site}

# 윈도우 환경 Playwright 에러 완벽 해결을 위한 실행 블록
if __name__ == "__main__":
    import uvicorn
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    uvicorn.run(app, host="0.0.0.0", port=8000)