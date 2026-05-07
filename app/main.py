import os
import asyncio
from integrated_analyzer import DebugIntegratedAnalyzer
from db_handler import save_analysis_results
from database import SessionLocal
from playwright.async_api import async_playwright
from database import engine, Base
import models  # 모델들을 불러와야 Base가 인식합니다.
from sqlalchemy import text

async def login_and_save_session(target_url):
    """사용자가 직접 로그인할 수 있도록 브라우저를 띄우고 세션을 저장합니다."""
    auth_dir = "auth"
    os.makedirs(auth_dir, exist_ok=True)
    auth_path = os.path.join(auth_dir, "login_state.json")

    async with async_playwright() as p:
        
        print(f"\n🔑 [LOGIN] {target_url} 로그인 세션을 생성합니다.")
        # 사용자가 직접 로그인해야 하므로 headless=False 필수
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(target_url)
        
        print("\n" + "!" * 50)
        print("1. 브라우저에서 로그인을 완료해 주세요.")
        print("2. 로그인이 끝나면 터미널로 돌아와 'Enter'를 누르세요.")
        print("!" * 50 + "\n")

        # 사용자의 입력을 대기 (동기식 input을 위해 루프 활용 가능하지만 간단히 처리)
        await asyncio.get_event_loop().run_in_executor(None, input, "로그인을 완료했나요? (Enter 입력): ")

        # 현재 상태(쿠키, 로컬스토리지 등) 저장
        await context.storage_state(path=auth_path)
        print(f"✅ 세션 저장 완료: {auth_path}")
        
        await browser.close()
    return auth_path

async def start_pipeline():
    # 0. DB 테이블 생성 (테이블이 없을 경우에만 생성함)
    print("\n[DB] 🧹 기존 데이터를 삭제하고 초기화합니다...")
    with engine.connect() as conn:
        # 외래키 제약 조건을 잠시 끄고 데이터를 비웁니다 (MySQL 기준)
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        conn.execute(text("TRUNCATE TABLE board_structures;"))
        conn.execute(text("TRUNCATE TABLE sites;"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        conn.commit()
    
    # 테이블 구조 다시 확인
    Base.metadata.create_all(bind=engine)
    print("[DB] ✅ 초기화 완료. 깨끗한 상태에서 시작합니다.")

    # 1. CLI 입력
    print("\n--- Site Structure Sniffer & Filter Analyzer ---")
    target_url = input("분석할 사이트 URL을 입력하세요 (예: https://zeropage.org): ").strip()
    
    if not target_url.startswith("http"):
        print("❌ 유효한 URL을 입력해 주세요.")
        return

    # 2. 로그인 및 세션 저장
    await login_and_save_session(target_url)

    # 3. 분석 엔진 가동 (저장된 세션 자동 로드)
    analyzer = DebugIntegratedAnalyzer(target_url, max_pages=30, delay_range=(1.5, 2.5))
    results = await analyzer.run()

    # 4. RDS 저장
    db = SessionLocal()
    try:
        domain = urlparse(target_url).netloc
        save_analysis_results(db, domain, results)
    except Exception as e:
        print(f"❌ DB 저장 중 오류 발생: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    from urllib.parse import urlparse
    asyncio.run(start_pipeline())