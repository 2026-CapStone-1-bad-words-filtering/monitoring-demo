import sys
import os
import json
import asyncio
import httpx
import websockets
from urllib.parse import urlparse
import ast
import httpx
from fastapi import Request
from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from json import dumps

# 기존 프로젝트 내부 모듈 참조
from . import database, models, db_handler, schemas
from .models import Site, BoardStructure  # 스키마
from .database import get_db, Base        # DB 세션 설정

app = FastAPI(title="Filtering System Control Tower")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB 테이블 자동 생성
models.Base.metadata.create_all(bind=database.engine)
INFERENCE_ENGINE_URL = "http://inference-engine:8080/detect"

# DB 의존성 주입 함수
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# [1] 원격 로그인 세션 관리 (WebSocket Proxy)
# ==========================================

@app.post("/auth/start-login")
async def start_login(payload: dict):
    """analyzer(수집기) 컨테이너에게 브라우저 인스턴스 생성을 요청합니다."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("http://analyzer:8000/auth/start-session", json=payload)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analyzer 연결 실패: {str(e)}")

@app.websocket("/ws/remote-login/{session_id}")
async def proxy_remote_login(websocket: WebSocket, session_id: str):
    """
    프론트엔드와 analyzer 사이의 브라우저 화면/입력 데이터를 실시간 중계합니다.
    """
    await websocket.accept()
    
    # analyzer 컨테이너의 내부 WebSocket 주소
    analyzer_ws_url = f"ws://analyzer:8000/ws/stream/{session_id}"
    
    try:
        async with websockets.connect(analyzer_ws_url) as analyzer_ws:
            async def forward_to_analyzer():
                """사용자의 입력을 analyzer로 전달"""
                async for message in websocket.iter_text():
                    await analyzer_ws.send(message)

            async def forward_to_client():
                """analyzer의 화면 데이터를 클라이언트로 전달"""
                async for message in analyzer_ws:
                    await websocket.send_bytes(message)

            # 두 태스크를 병렬로 실행하여 양방향 통신 유지
            await asyncio.gather(forward_to_analyzer(), forward_to_client())
            
    except WebSocketDisconnect:
        print(f"[WS] Client disconnected: {session_id}")
    except Exception as e:
        print(f"[WS_ERROR] Proxy error: {e}")
    finally:
        await websocket.close()


# ==========================================
# [2] 실시간 분석 로그 중계 및 DB 자동 저장 (Proxy)
# ==========================================

@app.get("/analyze/stream")
async def proxy_analysis_stream(
    url: str = Query(..., description="분석할 대상 URL"), 
    db: Session = Depends(get_db)
):
    """
    Analyzer의 크롤링 과정을 프론트로 스트리밍하고, 최종 결과는 가로채서 DB에 저장합니다.
    """
    async def log_proxy():
        analyzer_base_url = "http://analyzer:8000/analyze/stream"
        
        async with httpx.AsyncClient(timeout=None) as client:
            # url 파라미터를 params로 넘겨 안전하게 인코딩 처리
            async with client.stream("GET", analyzer_base_url, params={"url": url}) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    # 1. 프론트엔드로 로그를 중계 (스트리밍)
                    yield f"{line}\n\n"
                    
                    # 2. 결과값이 떨어지면 가로채서 파싱 후 DB에 저장
                    if "[RESULT]" in line:
                        try:
                            result_json_str = line.split("[RESULT] ")[-1].strip()
                            analysis_data = json.loads(result_json_str)
                            domain = urlparse(url).netloc
                            
                            # 🚀 DB 저장은 스레드풀로 넘겨 메인 API 서버 블로킹 방지
                            await run_in_threadpool(
                                db_handler.save_analysis_results, 
                                db, 
                                domain, 
                                analysis_data
                            )
                            yield f"[SYSTEM] {domain} 구조 분석 데이터 DB 저장 완료\n\n"
                            
                        except Exception as e:
                            yield f"[DB_ERROR] 데이터 저장 실패: {str(e)}\n\n"

    return StreamingResponse(log_proxy(), media_type="text/event-stream")


# ==========================================
# [3] 설정값 조회 API (수집 스크립트용)
# ==========================================

@app.get("/config/{domain}")
async def get_site_config(domain: str, db: Session = Depends(get_db)):
    """DB에 저장된 특정 사이트의 구조 데이터를 반환합니다."""
    site = db.query(Site).filter(Site.domain == domain).first()
    if not site:
        raise HTTPException(status_code=404, detail="등록되지 않은 도메인입니다.")
    
    structures = db.query(BoardStructure).filter(BoardStructure.site_id == site.id).all()
    return {"domain": domain, "boards": structures}


# ==========================================
# [4] 실시간 필터링 API (Inference Engine 연동)
# ==========================================

@app.post("/filter")
async def filter_content(payload: schemas.FilterRequest):
    """
    프론트에서 들어온 텍스트를 AI 모델 서버(inference-engine)로 보내 판별합니다.
    """
    async with httpx.AsyncClient() as client:
        try:
            # inference-engine 컨테이너 호출 (포트 8080 가정)
            response = await client.post(
                "http://inference-engine:8080/predict", 
                json={"text": payload.content},
                timeout=5.0
            )
            result = response.json()
            is_bad = result.get("is_bad", False)
            
            return {
                "is_filtered": is_bad,
                "action": "mask" if is_bad else "none",
                "modified_content": payload.content.replace(payload.content, "***") if is_bad else payload.content
            }
        except Exception as e:
            # 모델 서버 장애 시 기본 키워드 필터링(Fallback) 작동
            print(f"[AI_ERROR] Inference engine 호출 실패: {e}")
            is_bad = "욕설" in payload.content
            return {
                "is_filtered": is_bad,
                "action": "mask" if is_bad else "none",
                "modified_content": payload.content.replace("욕설", "***") if is_bad else payload.content
            }

# ==========================================
# [5] 동적 JS SDK 에이전트 발급
# ==========================================

@app.get("/agent/{domain}.js")
async def get_domain_agent(domain: str, db: Session = Depends(get_db)):
    # 1. DB에서 사이트 조회
    site = db.query(Site).filter(Site.domain == domain).first()

    if not site:
        if "localhost" in domain or "127.0.0.1" in domain:
            site = db.query(Site).filter(
                Site.domain.contains("host.docker.internal")
            ).first()

        if not site:
            raise HTTPException(
                status_code=404,
                detail=f"Site not found: {domain}"
            )

    # 2. 구조 정보 병합
    structures = db.query(BoardStructure).filter(
        BoardStructure.site_id == site.id
    ).all()

    all_selectors = []

    print(f"\n--- [에이전트 발급 디버깅: {domain}] ---")
    print(f"찾아낸 게시판 구조 개수: {len(structures)}개")

    for struct in structures:
        db_selectors = struct.get_selectors

        print(f"[DEBUG] 꺼낸 데이터: {db_selectors}")
        print(f"[DEBUG] 데이터 타입: {type(db_selectors)}")

        if db_selectors:

            if isinstance(db_selectors, str):
                try:
                    db_selectors = json.loads(db_selectors)

                except json.JSONDecodeError:
                    try:
                        db_selectors = ast.literal_eval(db_selectors)

                    except Exception as e:
                        print(f"[DB_PARSE_ERROR] 변환 실패 ({struct.mid}): {e}")
                        continue

            if isinstance(db_selectors, list):
                all_selectors.extend(db_selectors)
            else:
                all_selectors.append(db_selectors)

    print(f"최종 병합된 셀렉터 개수: {len(all_selectors)}개\n")

    js_selectors_payload = dumps(all_selectors, ensure_ascii=False)

    # ==========================================
    # JS SDK 생성
    # ==========================================

    js_template = f"""
        (function() {{
            'use strict';

            // 1. 무한 루프 방지를 위해 원본 객체 미리 저장
            const originalFetch = window.fetch;
            const originalXHROpen = XMLHttpRequest.prototype.open;
            const originalXHRSend = XMLHttpRequest.prototype.send;

            console.log("[SDK][INIT] '{site.site_name}' 에이전트 활성화");
            console.log("[SDK][INIT] 모든 감시 모듈(DOM + Network) 활성화");

            const targetSelectors = {js_selectors_payload};
            const SCAN_DELAY = 300;
            const textCache = new Map();

            // ==========================================
            // [1] 네트워크 후킹 (Fetch + XHR 통합 차단)
            // ==========================================
            function hookNetwork() {{
                // --- Fetch 후킹 ---
                window.fetch = async (...args) => {{
                    let [resource, config] = args;
                    
                    // 필터링 API 자신에게 보내는 요청은 즉시 통과
                    if (typeof resource === 'string' && resource.includes('/api/detect')) {{
                        return originalFetch(...args);
                    }}

                    const method = config ? (config.method || 'GET') : 'GET';
                    const body = config ? config.body : null;
                    const url = resource;
                    
                    // POST면 Body, GET이면 URL 전체를 검사 대상으로 설정
                    const contentToCheck = (method === 'POST') ? 
                                        (typeof body === 'string' ? body : JSON.stringify(body)) : 
                                        url;

                    const result = await detectBadContent(contentToCheck);
                    if (result.isInappropriate) {{
                        alert("🚫 보안 정책에 의해 차단되었습니다: " + result.reason);
                        console.warn("[SDK][🚫 BLOCK] 네트워크 요청 차단:", contentToCheck);
                        throw new Error("보안 정책상 차단된 요청입니다.");
                    }}
                    
                    return originalFetch(...args);
                }};

                // --- XHR 후킹 (jQuery/Axios 대응) ---
                XMLHttpRequest.prototype.open = function(method, url) {{
                    this._url = url;
                    this._method = method;
                    return originalXHROpen.apply(this, arguments);
                }};

                XMLHttpRequest.prototype.send = async function(body) {{
                    if (this._url && this._url.includes('/api/detect')) return originalXHRSend.apply(this, arguments);
                    
                    const contentToCheck = (this._method === 'POST') ? (body || "") : this._url;
                    
                    const result = await detectBadContent(contentToCheck);
                    if (result.isInappropriate) {{
                        alert("🚫 보안 정책에 의해 차단되었습니다: " + result.reason);
                        console.warn("[SDK][🚫 BLOCK] XHR 요청 차단:", contentToCheck);
                        this.abort(); 
                        return;
                    }}
                    return originalXHRSend.apply(this, arguments);
                }};
            }}

            // ==========================================
            // [2] AI 검사 공통 함수
            // ==========================================
            async function detectBadContent(text) {{
                if (!text || text.trim() === "") return {{ isInappropriate: false }};
                const checkText = text.length > 2000 ? text.substring(0, 2000) : text;
                if (textCache.has(checkText)) return textCache.get(checkText);

                try {{
                    // 무조건 원본 fetch 사용
                    const response = await originalFetch("http://localhost:8000/api/detect", {{
                        method: "POST",
                        headers: {{ "Content-Type": "application/json" }},
                        body: JSON.stringify({{ text: checkText }})
                    }});

                    const result = await response.json();
                    textCache.set(checkText, result);
                    return result;
                }} catch (err) {{
                    console.error("[SDK][AI_ERROR]", err);
                    return {{ isInappropriate: false, reason: "AI 서버 오류" }};
                }}
            }}

            // ==========================================
            // [3] DOM 필터링 관련 함수들
            // ==========================================
            function buildQuery(selectorObj) {{
                if (!selectorObj || !selectorObj.tag) return null;
                let query = selectorObj.tag;
                if (selectorObj.className) {{
                    const classes = selectorObj.className.split(' ').filter(c => c).join('.');
                    if (classes) query += `.${{classes}}`;
                }}
                return query;
            }}

            async function filterElement(el) {{
                if (!el || el.dataset.isFiltered === "true") return;
                const text = el.innerText || el.textContent;
                if (!text || !text.trim()) return;

                const result = await detectBadContent(text);
                if (result.isInappropriate) {{
                    console.warn("[SDK][🚫 DETECTED] 화면 콘텐츠 차단:", result.reason);
                    el.dataset.originalHtml = el.innerHTML;
                    el.innerHTML = `<div style="padding:10px; border:2px solid red; background:#fff5f5; color:red; font-weight:bold; border-radius:8px;">🚫 부적절한 콘텐츠 차단</div>`;
                }}
                el.dataset.isFiltered = "true";
            }}

            async function checkAndFilter() {{
                for (const sel of targetSelectors) {{
                    const query = buildQuery(sel);
                    if (!query) continue;
                    const elements = document.querySelectorAll(query);
                    for (const el of elements) {{ await filterElement(el); }}
                }}
            }}

            // ==========================================
            // [4] 시작 및 초기화
            // ==========================================
            async function init() {{
                hookNetwork(); // 네트워크 감시 시작 (GET/POST/XHR)
                await checkAndFilter();
            }}

            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', init);
            }} else {{
                init();
            }}

            const observer = new MutationObserver(() => {{
                setTimeout(checkAndFilter, SCAN_DELAY);
            }});
            observer.observe(document.body, {{ childList: true, subtree: true, characterData: true }});

            console.log("[SDK][READY] SDK 모든 기능 활성화 완료");
        }})();
        """

    return Response(
        content=js_template,
        media_type="application/javascript"
    )

@app.post("/api/detect")
async def detect_proxy(request: Request):
    # 1. 프론트에서 데이터 수신
    try:
        data = await request.json()
        text = data.get("text", "")
        print(f"\n[DEBUG] 🚀 프론트 요청 수신: '{text}'")
    except Exception as e:
        print(f"[DEBUG] ❌ JSON 파싱 실패: {e}")
        return {"isInappropriate": False, "reason": "데이터 파싱 에러"}

    # 2. AI 서버 데이터 변환
    request_to_ai = {"content": text}
    
    # 3. 인퍼런스 엔진 호출
    async with httpx.AsyncClient() as client:
        try:
            print(f"[DEBUG] 🤖 Inference Engine({INFERENCE_ENGINE_URL})으로 전달 중...")
            
            response = await client.post(INFERENCE_ENGINE_URL, json=request_to_ai, timeout=5.0)
            
            # 응답 상태 확인
            if response.status_code != 200:
                print(f"[DEBUG] ⚠️ AI 서버 에러(Status {response.status_code}): {response.text}")
                return {"isInappropriate": False, "reason": "AI 서버 응답 오류"}
            
            ai_result = response.json()
            print(f"[DEBUG] ✅ AI 서버 최종 응답: {ai_result}")
            
            return ai_result
            
        except Exception as e:
            print(f"[DEBUG] ❌ 통신 에러 발생: {str(e)}")
            return {"isInappropriate": False, "reason": "AI 서버 연결 실패"}