import sys
import os
import json
import asyncio
import httpx
import websockets
from urllib.parse import urlparse
import ast

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
    # 1. DB에서 사이트 조회 (도커 내부망 테스트 우회 포함)
    site = db.query(Site).filter(Site.domain == domain).first()
    
    if not site:
        # 프론트엔드가 localhost로 요청해도, DB에 저장된 host.docker.internal을 찾도록 매핑
        if "localhost" in domain or "127.0.0.1" in domain:
            site = db.query(Site).filter(Site.domain.contains("host.docker.internal")).first()
        
        if not site:
            raise HTTPException(status_code=404, detail=f"Site not found: {domain}")

    # 2. 구조 정보 가져와서 배열 병합
    structures = db.query(BoardStructure).filter(BoardStructure.site_id == site.id).all()
    all_selectors = []
    
    print(f"\n--- [에이전트 발급 디버깅: {domain}] ---")
    print(f"찾아낸 게시판 구조 개수: {len(structures)}개")

    for struct in structures:
        db_selectors = struct.get_selectors 
        print(f"[DEBUG] 꺼낸 데이터: {db_selectors}")
        print(f"[DEBUG] 데이터 타입: {type(db_selectors)}")
        
        if db_selectors:
            # 문자열(str)로 튀어나올 경우 강제 변환
            if isinstance(db_selectors, str):
                try:
                    # 1차 시도: 표준 JSON 파싱
                    db_selectors = json.loads(db_selectors)
                except json.JSONDecodeError:
                    try:
                        # 2차 시도: 싱글 쿼터 등 파이썬 딕셔너리 문자열 강제 파싱
                        db_selectors = ast.literal_eval(db_selectors)
                    except Exception as e:
                        print(f"[DB_PARSE_ERROR] 변환 완전 실패 ({struct.mid}): {e}")
                        continue
                        
            # 리스트면 통째로 합치고, 딕셔너리면 리스트 안에 넣기
            if isinstance(db_selectors, list):
                all_selectors.extend(db_selectors)
            else:
                all_selectors.append(db_selectors)

    print(f"최종 병합된 셀렉터 개수: {len(all_selectors)}개\n")

    js_selectors_payload = dumps(all_selectors, ensure_ascii=False)

    # 3. [연동 전용] 텍스트 필터링/수정 스크립트 템플릿
    # 3. [연동 전용] 텍스트 필터링/수정 및 패킷 차단 스크립트 템플릿
    js_template = f"""
    (function() {{
        'use strict';
        console.log("[SDK][INIT] '{site.site_name}' 에이전트 활성화 (도메인: {domain})");
        
        // ==========================================
        // 🚨 [네트워크 검문소] POST 요청 가로채기 (fetch & XHR)
        // ==========================================
        const BAD_WORD = "시발";

        // 1. Fetch API 가로채기 (최신 React/Next.js 주력 통신)
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {{
            const [resource, config] = args;
            
            if (config && config.method && config.method.toUpperCase() === 'POST' && config.body) {{
                let bodyText = "";
                
                if (typeof config.body === 'string') {{
                    bodyText = config.body;
                }} else if (config.body instanceof FormData) {{
                    for (let [key, value] of config.body.entries()) {{
                        bodyText += value + " ";
                    }}
                }}

                if (bodyText.includes(BAD_WORD)) {{
                    console.error(`[SDK][🚫 차단] fetch POST 요청에 금칙어('${{BAD_WORD}}')가 감지되어 전송을 차단합니다.`);
                    alert("⚠️ 금칙어가 포함되어 글을 등록할 수 없습니다.");
                    // 서버로 패킷을 보내지 않고 프론트엔드 단에서 에러로 터뜨림
                    return Promise.reject(new Error("욕설 필터링에 의해 요청이 차단되었습니다."));
                }}
            }}
            return originalFetch.apply(this, args);
        }};

        // 2. XMLHttpRequest (XHR) 가로채기 (구형 라이브러리 및 AJAX 대응)
        const originalXHRSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function(body) {{
            if (body) {{
                let bodyText = "";
                
                if (typeof body === 'string') {{
                    bodyText = body;
                }} else if (body instanceof FormData) {{
                    for (let [key, value] of body.entries()) {{
                        bodyText += value + " ";
                    }}
                }}

                if (bodyText.includes(BAD_WORD)) {{
                    console.error(`[SDK][🚫 차단] XHR POST 요청에 금칙어('${{BAD_WORD}}')가 감지되어 전송을 차단합니다.`);
                    alert("⚠️ 금칙어가 포함되어 글을 등록할 수 없습니다.");
                    this.abort(); // 통신 강제 취소
                    return; 
                }}
            }}
            originalXHRSend.apply(this, arguments);
        }};
        // ==========================================

        const targetSelectors = {js_selectors_payload}; 
        console.log("[SDK][INIT] DB에서 가져온 쿼리 총 개수:", targetSelectors.length, "개");
        
        function buildQuery(selectorObj) {{
            if (!selectorObj || !selectorObj.tag) return null;
            let query = selectorObj.tag;
            if (selectorObj.className) {{
                const classes = selectorObj.className.split(' ').filter(c => c).join('.');
                if (classes) query += `.${{classes}}`;
            }}
            return query;
        }}

        function checkAndFilter(triggerSource) {{
            let activeMatchCount = 0;
            
            targetSelectors.forEach(sel => {{
                const query = buildQuery(sel);
                if (!query) return;

                const elements = document.querySelectorAll(query);
                
                if (elements.length > 0) {{
                    activeMatchCount += elements.length;
                }}

                elements.forEach(el => {{
                    if (el.dataset.isFiltered === "true") return;
                    
                    const text = el.innerText || el.textContent;
                    if (!text || !text.trim()) return;

                    // 🚀 [UI 렌더링 보호] HTML 구조를 파괴하지 않고 단어만 마스킹
                    let htmlContent = el.innerHTML;
                    const badWords = ["시발", "미친"];
                    let isModified = false;

                    badWords.forEach(word => {{
                        if (htmlContent.includes(word)) {{
                            console.log(`[SDK][🎯 MATCH] 쿼리('${{query}}') -> 비속어 발견`);
                            // 정규식을 사용해 태그 속성은 건드리지 않고 텍스트만 치환
                            const regex = new RegExp(word, 'g');
                            htmlContent = htmlContent.replace(regex, `<span style="color:red; font-weight:bold;">🚫금칙어🚫</span>`);
                            isModified = true;
                        }}
                    }});

                    if (isModified) {{
                        console.log(`[SDK][🚫 FILTER] 안전한 마스킹 처리 완료`);
                        el.innerHTML = htmlContent;
                    }}
                    
                    // 검사 완료된 엘리먼트는 꼬리표를 달아 무한루프 방지
                    el.dataset.isFiltered = "true";
                }});
            }});
            
            if (activeMatchCount === 0 && triggerSource === "PAGE_LOAD") {{
                console.warn("[SDK][⚠️ WARN] 현재 화면 구조는 DB에 저정된 셀렉터 지도와 일치하지 않습니다. 수집 모듈(Analyzer)로 이 페이지를 스캔해야 합니다.");
            }}
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', () => checkAndFilter("PAGE_LOAD"));
        }} else {{
            checkAndFilter("PAGE_LOAD");
        }}

        let isFiltering = false;
        let lastUrl = window.location.href;

        const observer = new MutationObserver((mutations) => {{
            if (isFiltering) return;

            const currentUrl = window.location.href;
            const isPageChanged = currentUrl !== lastUrl;
            
            if (isPageChanged) {{
                lastUrl = currentUrl;
                console.log(`[SDK][🔄 ROUTE] 페이지 이동 감지 -> ${{currentUrl}}`);
                isFiltering = true;
                checkAndFilter("PAGE_LOAD"); 
                isFiltering = false;
                return;
            }}

            const shouldScan = mutations.some(m => m.addedNodes.length > 0 || m.type === 'characterData');
            if (shouldScan) {{
                isFiltering = true; 
                checkAndFilter("DOM_CHANGE"); 
                isFiltering = false; 
            }}
        }});
        
        observer.observe(document.body, {{ childList: true, subtree: true, characterData: true }});
    }})();
    """

    return Response(content=js_template, media_type="application/javascript")
        
# 정적 파일 서빙
app.mount("/static", StaticFiles(directory="static"), name="static")