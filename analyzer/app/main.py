import asyncio
import json
from fastapi import FastAPI, WebSocket, Body, Query
from fastapi.responses import StreamingResponse

# 구현해두신 모듈들 임포트
from .remote_login import RemoteLoginManager
from .integrated_analyzer import IntegratedAnalyzer # 구조 분석기 클래스

app = FastAPI(title="Analyzer Engine")
login_manager = RemoteLoginManager()

# --- [1. 원격 로그인 세션 생성 (API 서버가 호출)] ---
@app.post("/auth/start-session")
async def start_login_session(payload: dict = Body(...)):
    """
    api-server의 요청을 받아 브라우저 인스턴스를 생성하고 대기합니다.
    """
    url = payload.get("url")
    if not url:
        return {"status": "error", "message": "URL이 누락되었습니다."}
        
    session_id = await login_manager.create_session(url)
    
    return {
        "status": "success", 
        "session_id": session_id, 
        "stream_url": f"/ws/stream/{session_id}"
    }

# --- [2. 브라우저 화면 스트리밍 및 입력 제어 (WebSocket)] ---
@app.websocket("/ws/stream/{session_id}")
async def stream_browser(websocket: WebSocket, session_id: str):
    """
    사용자의 브라우저 화면을 중계하고, 마우스/키보드 입력을 전달받습니다.
    세션 저장이 완료되면 이 소켓은 닫힙니다.
    """
    await login_manager.handle_streaming(websocket, session_id)


# --- [3. 캡스톤 핵심: 구조 분석 가동 및 실시간 로그 스트리밍] ---
@app.get("/analyze/stream")
async def analyze_target_site(url: str = Query(..., description="분석할 타겟 URL")):
    """
    저장된 로그인 세션(login_state.json)을 활용하여
    게시판 구조를 분석(Crawling)하고 결과를 실시간으로 스트리밍합니다.
    """
    async def log_generator():
        yield f"[SYSTEM] {url} 분석 초기화 중...\n"
        
        try:
            # 1. 분석기 인스턴스 생성 (아까 구워둔 세션 파일 탑재!)
            # 파일 경로는 remote_login.py에서 저장한 위치와 동일해야 합니다.
            analyzer = IntegratedAnalyzer(start_url=url)
            
            yield "[SYSTEM] 분석기 가동 완료. 타겟 탐색을 시작합니다.\n"
            
            # 2. 분석 실행 
            # (주의: integrated_analyzer.py의 analyze 메서드가 
            # async for로 yield를 뱉도록 설계되어 있다고 가정합니다.)
            async for log_msg in analyzer.analyze():
                yield f"{log_msg}\n"
                await asyncio.sleep(0.05) # 출력 버퍼링 방지용 약간의 딜레이
                
            # 만약 analyze()가 yield가 아니라 통째로 return을 하는 구조라면, 
            # 위 루프 대신 아래처럼 쓰시면 됩니다.
            # result_data = await analyzer.analyze()
            # yield f"[RESULT_DATA] {json.dumps(result_data)}\n"

            yield "[SYSTEM] 모든 구조 분석이 완료되었습니다.\n"

        except Exception as e:
            yield f"[ERROR] 분석 중 치명적 오류 발생: {str(e)}\n"
            
    # 청크(Chunk) 단위로 텍스트를 스트리밍하여 API 서버로 전달
    return StreamingResponse(log_generator(), media_type="text/plain")