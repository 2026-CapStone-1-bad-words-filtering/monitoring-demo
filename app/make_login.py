import sys
import asyncio
from playwright.async_api import async_playwright

async def manual_login(target_url):
    print(f"🚀 수동 로그인 세션 생성기를 시작합니다... (타겟: {target_url})")
    
    async with async_playwright() as p:
        # 화면을 띄워서 사람이 직접 조작할 수 있게 합니다.
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            await page.goto(target_url)
        except Exception as e:
            print(f"⚠️ 사이트 접속 실패: {e}")
            await browser.close()
            return
            
        print("=====================================================")
        print(f"🌐 {target_url} 에 접속했습니다.")
        print("⏳ 지금부터 20초의 시간이 주어집니다!")
        print("🧑‍💻 뜬 브라우저 창에서 직접 아이디/비번을 치고 로그인을 완료해 주세요.")
        print("=====================================================")
        
        # 사람이 로그인할 수 있도록 20초(20000ms) 동안 얌전히 대기
        await page.wait_for_timeout(20000) 
        
        # 20초 경과 시점의 브라우저 상태(쿠키, 세션 등)를 파일로 저장
        await context.storage_state(path="auth/login_state.json")
        
        print("✅ 시간 종료! 현재 로그인 상태가 'login_state.json'에 저장되었습니다.")
        
        await browser.close()

if __name__ == "__main__":
    # 1. 터미널 명령줄 인수로 URL을 전달받은 경우 (예: python make_login.py https://naver.com)
    if len(sys.argv) > 1:
        url_input = sys.argv[1]
    # 2. 그냥 실행한 경우 (예: python make_login.py), 화면에서 직접 입력받음
    else:
        url_input = input("🔗 로그인할 사이트 URL을 입력하세요 (예: https://zeropage.org): ").strip()
        
    # http/https 가 없으면 자동으로 붙여줌
    if not url_input.startswith("http"):
        url_input = "https://" + url_input

    asyncio.run(manual_login(url_input))