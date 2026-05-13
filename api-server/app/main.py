from fastapi.staticfiles import StaticFiles
import httpx
import json
import asyncio
import websockets
from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from urllib.parse import urlparse

# 기존 프로젝트 파일 참조
from . import database, models, db_handler, schemas

app = FastAPI(title="Filtering System Control Tower")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

models.Base.metadata.create_all(bind=database.engine)

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- [1. 원격 로그인 세션 관리: WebSocket Proxy 구현] ---

@app.post("/auth/start-login")
async def start_login(payload: dict):
    """analyzer에게 브라우저 인스턴스 생성을 요청합니다."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("http://analyzer:8000/auth/start-session", json=payload)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analyzer 연결 실패: {str(e)}")

@app.websocket("/ws/remote-login/{session_id}")
async def proxy_remote_login(websocket: WebSocket, session_id: str):
    """
    프론트엔드와 analyzer 사이의 브라우저 화면/입력 데이터를 중계합니다.
    (비어있던 1번 로직 완성)
    """
    await websocket.accept()
    
    # analyzer 컨테이너의 내부 WebSocket 주소
    analyzer_ws_url = f"ws://analyzer:8000/ws/stream/{session_id}"
    
    try:
        async with websockets.connect(analyzer_ws_url) as analyzer_ws:
            # 양방향 통신을 위한 Task 생성
            async def forward_to_analyzer():
                """사용자의 입력을 analyzer로 전달"""
                async for message in websocket.iter_text():
                    await analyzer_ws.send(message)

            async def forward_to_client():
                """analyzer의 화면 데이터를 클라이언트로 전달"""
                async for message in analyzer_ws:
                    await websocket.send_bytes(message)

            # 두 태스크를 병렬로 실행
            await asyncio.gather(forward_to_analyzer(), forward_to_client())
            
    except WebSocketDisconnect:
        print(f"[WS] Client disconnected: {session_id}")
    except Exception as e:
        print(f"[WS_ERROR] Proxy error: {e}")
    finally:
        await websocket.close()


# --- [2. 실시간 분석 로그 중계 및 DB 자동 저장] ---

@app.get("/analyze/stream")
async def proxy_analysis_stream(
    url: str = Query(..., description="분석할 대상 URL"), 
    db: Session = Depends(get_db)
):
    async def log_proxy():
        analyzer_url = f"http://analyzer:8000/analyze/stream?url={url}"
        
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", analyzer_url) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    if "RESULT_DATA" in line:
                        try:
                            #
                            result_json_str = line.split("RESULT_DATA] ")[-1].strip()
                            analysis_data = json.loads(result_json_str)
                            
                            domain = urlparse(url).netloc
                            db_handler.save_analysis_results(db, domain, analysis_data) #
                            
                            print(f"[SYSTEM] {domain} 구조 분석 데이터 저장 완료")
                        except Exception as e:
                            print(f"[DB_ERROR] 데이터 저장 실패: {e}")
                    
                    yield f"{line}\n\n"

    return StreamingResponse(log_proxy(), media_type="text/event-stream")


# --- [3. 설정값 조회 API: 수집 스크립트용] ---

@app.get("/config/{domain}")
async def get_site_config(domain: str, db: Session = Depends(get_db)):
    #
    site = db.query(models.Site).filter(models.Site.domain == domain).first()
    if not site:
        raise HTTPException(status_code=404, detail="등록되지 않은 도메인입니다.")
    
    structures = db.query(models.BoardStructure).filter(models.BoardStructure.site_id == site.id).all()
    return {"domain": domain, "boards": structures}


# --- [4. 실시간 필터링 API: Inference Engine 연동] ---

@app.post("/filter")
async def filter_content(payload: schemas.FilterRequest):
    """
    실제 AI 모델 서버(inference-engine)와 통신하여 결과를 판별합니다.
    (비어있던 4번 로직 완성)
    """
    async with httpx.AsyncClient() as client:
        try:
            # inference-engine 컨테이너의 모델 추론 API 호출
            # (포트 8080은 docker-compose 설정 기준)
            response = await client.post(
                "http://inference-engine:8080/predict", 
                json={"text": payload.content},
                timeout=5.0
            )
            result = response.json()
            
            # 모델 서버 응답 규격 예시: {"is_bad": true, "confidence": 0.98}
            is_bad = result.get("is_bad", False)
            
            return {
                "is_filtered": is_bad,
                "action": "mask" if is_bad else "none",
                "modified_content": payload.content.replace(payload.content, "***") if is_bad else payload.content
            }
        except Exception as e:
            # 모델 서버 연결 실패 시 기본 필터링(욕설 키워드)으로 백업
            print(f"[AI_ERROR] Inference engine 호출 실패: {e}")
            is_bad = "욕설" in payload.content
            return {
                "is_filtered": is_bad,
                "action": "mask" if is_bad else "none",
                "modified_content": payload.content.replace("욕설", "***") if is_bad else payload.content
            }
        
app.mount("/static", StaticFiles(directory="static"), name="static")