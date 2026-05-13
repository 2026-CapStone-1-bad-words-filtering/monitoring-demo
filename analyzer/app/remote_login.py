import asyncio
import json
from playwright.async_api import async_playwright

class RemoteLoginManager:
    def __init__(self):
        self.sessions = {}

    async def create_session(self, url):
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 좌표 일치를 위해 뷰포트 고정
        await page.set_viewport_size({"width": 1280, "height": 1024})
        await page.goto(url)

        session_id = str(id(page))
        self.sessions[session_id] = {
            "browser": browser,
            "page": page,
            "playwright": playwright
        }
        return session_id

    async def handle_streaming(self, websocket, session_id):
        await websocket.accept()
        session = self.sessions.get(session_id)
        if not session:
            await websocket.close()
            return

        page = session["page"]
        stop_event = asyncio.Event()

        # [루프 1] 화면 데이터 전송 (Screen Stream)
        async def stream_loop():
            try:
                while not stop_event.is_set():
                    screenshot = await page.screenshot(type="jpeg", quality=60)
                    await websocket.send_bytes(screenshot)
                    await asyncio.sleep(0.1) # 약 10 FPS
            except Exception as e:
                print(f"[STREAM] 종료: {e}")
            finally:
                stop_event.set()

        # [루프 2] 사용자 입력 수신 (Input Relay)
        async def input_loop():
            try:
                while not stop_event.is_set():
                    data = await websocket.receive_json()
                    
                    if data.get("type") == "click":
                        await page.mouse.click(data['x'], data['y'])
                        print(f"[INPUT] Clicked: {data['x']}, {data['y']}")
                        
                    elif data.get("type") == "keydown":
                        # 키보드 입력 처리
                        await page.keyboard.press(data['key'])
                        print(f"[INPUT] Key Pressed: {data['key']}")
                        
                    elif data.get("type") == "save_session":
                        await page.context.storage_state(path="auth/login_state.json")
                        print("[AUTH] Storage State Saved.")
            except Exception as e:
                print(f"[INPUT] 종료: {e}")
            finally:
                stop_event.set()

        await asyncio.gather(stream_loop(), input_loop())

        # [정리] 세션 안전하게 삭제 (KeyError 방지)
        target = self.sessions.pop(session_id, None)
        if target:
            await target["browser"].close()
            await target["playwright"].stop()