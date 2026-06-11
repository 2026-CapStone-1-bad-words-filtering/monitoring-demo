import sys
import os
import json
import asyncio
import httpx
import ast
from typing import List, Optional
import websockets
from urllib.parse import urlparse

from fastapi import Request, FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from json import dumps
from sqlalchemy import func
from datetime import datetime, timedelta

# 프로젝트 내부 모듈 참조
from . import database, models, db_handler, schemas
from .models import Site, BoardStructure, User, UserSetting, DetectionLog
from .database import get_db, Base
from pydantic import BaseModel
from typing import List

app = FastAPI(title="Filtering System Control Tower")

# CORS 전면 개방 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class FilterProxyRequest(BaseModel):
    text: str
    user_id: int = 1
    threshold: float = 0.85  # 🚀 추가: 단건 검사 기본값 0.85

class BulkDetectRequest(BaseModel):
    texts: List[str]
    extension_mode: bool = True
    user_id: int = 1
    threshold: float = 0.85  # 🚀 추가: 대량/스트리밍 검사 기본값 0.85

INFERENCE_ENGINE_URL = "http://inference-engine:8080/detect"

# DB 테이블 자동 생성 및 초기화
print("\n[DB_INIT] 🗑️ 매 실행 시 초기화를 위해 기존 테이블을 전체 삭제합니다...")
Base.metadata.drop_all(bind=database.engine)
print("[DB_INIT] ✨ 신규 테이블을 생성합니다...")
Base.metadata.create_all(bind=database.engine)
print("[DB_INIT] ✅ 데이터베이스 테이블 준비 완료.\n")

# ==========================================
# [1] 원격 로그인 세션 관리 (WebSocket Proxy)
# ==========================================

@app.post("/auth/start-login")
async def start_login(payload: dict):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("http://filtering_analyzer:8000/auth/start-session", json=payload)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analyzer 연결 실패: {str(e)}")

@app.websocket("/ws/remote-login/{session_id}")
async def proxy_remote_login(websocket: WebSocket, session_id: str):
    await websocket.accept()
    analyzer_ws_url = f"ws://filtering_analyzer:8000/ws/stream/{session_id}"
    try:
        async with websockets.connect(analyzer_ws_url) as analyzer_ws:
            async def forward_to_analyzer():
                async for message in websocket.iter_text():
                    await analyzer_ws.send(message)
            async def forward_to_client():
                async for message in analyzer_ws:
                    await websocket.send_bytes(message)
            await asyncio.gather(forward_to_analyzer(), forward_to_client())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS_ERROR] Proxy error: {e}")
    finally:
        try: await websocket.close()
        except: pass


# ==========================================
# [2] 실시간 분석 로그 중계 및 구조 저장
# ==========================================

@app.get("/analyze/stream")
async def proxy_analysis_stream(url: str = Query(..., description="분석할 대상 URL"), user_id: Optional[int] = Query(None, description="유저 고유 ID"), db: Session = Depends(get_db)):
    target_user_id = user_id if user_id is not None else 1
    print(f"\n[STREAM_DEBUG] 🚀 실시간 로그 감시 및 듀얼 캡처 파이프라인 가동")

    async def log_proxy():
        analyzer_base_url = "http://filtering_analyzer:8000/analyze/stream"
        streamed_boards = {} 
        db_saved_flag = False
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", analyzer_base_url, params={"url": url}) as response:
                async for line in response.aiter_lines():
                    if not line: continue
                    yield f"{line}\n\n"
                    clean_line = line.strip()
                    
                    if "[PARSER]" in clean_line and "수집 완료:" in clean_line:
                        try:
                            parser_part = clean_line.split("[PARSER]")[-1].strip()
                            mid = parser_part.split("]")[0].replace("[", "").strip()
                            selector = parser_part.split("수집 완료:")[-1].strip()
                            if mid and selector:
                                if mid not in streamed_boards: streamed_boards[mid] = []
                                if selector not in streamed_boards[mid]: streamed_boards[mid].append(selector)
                        except: pass

                    if ("[RESULT]" in clean_line or "스캔 및 아키텍처 매핑 완료" in clean_line) and not db_saved_flag:
                        db_saved_flag = True
                        analysis_data = {}
                        if "[RESULT]" in clean_line:
                            try: analysis_data = json.loads(clean_line.split("[RESULT] ")[-1].strip())
                            except: pass
                        domain = url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0].strip()
                        try:
                            await run_in_threadpool(save_multi_site_analysis_results_logged, db, domain, analysis_data, target_user_id, streamed_boards)
                            yield f"[SYSTEM] 📊 다중 사이트 격리 완료: 도메인 '{domain}'이 자산으로 등록되었습니다.\n\n"
                        except Exception as e:
                            yield f"[DB_ERROR] 데이터 적재 실패: {str(e)}\n\n"
    return StreamingResponse(log_proxy(), media_type="text/event-stream")

def save_multi_site_analysis_results_logged(db: Session, domain: str, analysis_data: dict, user_id: int, streamed_boards: dict):
    clean_domain = domain.strip()
    site = db.query(models.Site).filter(models.Site.domain == clean_domain, models.Site.user_id == user_id).first()
    if not site:
        site = models.Site(domain=clean_domain, site_name=f"{clean_domain} 쇼핑몰", user_id=user_id)
        db.add(site)
        db.flush()
    setting = db.query(models.UserSetting).filter(models.UserSetting.site_id == site.id).first()
    if not setting:
        db.add(models.UserSetting(user_id=user_id, site_id=site.id, preferences={"banned_types": [], "is_active": True, "sensitivity": "medium", "custom_keywords": []}))
        db.flush()
    db.query(models.BoardStructure).filter(models.BoardStructure.site_id == site.id).delete()
    
    merged_boards = {}
    for mid, selectors in streamed_boards.items(): merged_boards[mid] = set(selectors)
    
    if isinstance(analysis_data, dict) and "boards" in analysis_data:
        for b in analysis_data.get("boards", []):
            b_mid = b.get("mid", "unknown")
            b_selectors = b.get("selectors", [])
            if b_mid not in merged_boards: merged_boards[b_mid] = set()
            if isinstance(b_selectors, list):
                for sel in b_selectors: merged_boards[b_mid].add(str(sel))
            elif isinstance(b_selectors, str): merged_boards[b_mid].add(b_selectors)
            
    for mid, selectors_set in merged_boards.items():
        db.add(models.BoardStructure(site_id=site.id, mid=mid, get_selectors=json.dumps(list(selectors_set), ensure_ascii=False), is_verified=True))
    db.commit()


# ==========================================
# [3] 설정값 및 AI 실시간 필터링 통신 API
# ==========================================

@app.get("/config/{domain}")
async def get_site_config(domain: str, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.domain == domain).first()
    if not site: raise HTTPException(status_code=404, detail="등록되지 않은 도메인입니다.")
    structures = db.query(BoardStructure).filter(BoardStructure.site_id == site.id).all()
    return {"domain": domain, "boards": structures}

def save_detection_log(user_id: int, stage: str, reason: str, blocked_content: str):
    db = database.SessionLocal()
    try:
        # 🚀 로그 저장 시 유저가 입력했던 순수 정제 텍스트를 함께 저장합니다.
        db.add(models.DetectionLog(
            user_id=user_id, 
            stage=stage, 
            reason=reason,
            blocked_content=blocked_content
        ))
        db.commit()
    except Exception as e: 
        db.rollback()
        print(f"[DB_ERROR] 로그 저장 중 예외 발생: {str(e)}")
    finally: 
        db.close()

@app.post("/api/detect")
async def detect_proxy(payload: schemas.FilterProxyRequest, background_tasks: BackgroundTasks):
    text = payload.text
    target_user_id = payload.user_id
    print(f"\n[DEBUG] 🚀 필터링 요청 수신 | User ID: {target_user_id} | 수신 데이터: '{text}'")
    print(f"\n[API_DEBUG] 🎯 단건 검사 | User ID: {payload.user_id} | 수신된 Threshold: {payload.threshold}")

    # 🛡️ 백엔드 이중 방어선: 혹시라도 프론트에서 JSON 껍데기가 들어오면 강제로 순수 텍스트만 도려냅니다.
    cleaned_text = text.strip()
    if cleaned_text.startswith('{') and cleaned_text.endswith('}'):
        try:
            parsed = json.loads(cleaned_text)
            if isinstance(parsed, dict):
                # 프레임워크 노이즈 필터링
                if parsed.get("frames") or parsed.get("isServer"):
                    return {"isInappropriate": False, "stage": "safe", "reason": "시스템 노이즈 패스"}
                # 알맹이 텍스트(value)만 긁어모아 결합
                text_values = [str(v) for v in parsed.values() if isinstance(v, str) and v.strip()]
                cleaned_text = " ".join(text_values)
                print(f"[DEBUG] ⚠️ JSON 껍데기 감지 -> 순수 텍스트 강제 정제 완료: '{cleaned_text}'")
        except:
            pass

    if not cleaned_text.strip():
        return {"isInappropriate": False, "stage": "safe", "reason": "내용 없음"}

    async with httpx.AsyncClient() as client:
        try:
            # 🚀 타임아웃 15초 유지 (로컬 BERT 연산 대기 보장)
            response = await client.post(
                INFERENCE_ENGINE_URL, 
                json={"content": cleaned_text, "threshold": payload.threshold}, 
                timeout=15.0
            )
            if response.status_code != 200:
                return {"isInappropriate": False, "reason": "AI 서버 응답 오류"}
            
            ai_result = response.json()
            print(f"[DEBUG] ✅ AI 서버 최종 응답: {ai_result}")
            
            if ai_result.get("isInappropriate"):
                # 🚀 [핵심] 네 번째 인자로 정제된 순수 텍스트(cleaned_text)를 함께 넘겨줍니다.
                background_tasks.add_task(
                    save_detection_log, 
                    user_id=target_user_id, 
                    stage=ai_result.get("stage", "unknown"), 
                    reason=ai_result.get("reason", "비속어 탐지"),
                    blocked_content=cleaned_text  # 👈 추가
                )
            return ai_result
        except Exception as e:
            print(f"[DEBUG] ❌ AI 서버 통신 에러: {str(e)}")
            return {"isInappropriate": False, "reason": "AI 서버 연결 실패"}


# ==============================================================================
# [4] ⚡ 동적 JS SDK 에이전트 발급 라우터 (DOM 파싱 로직 완벽 복원)
# ==============================================================================

@app.get("/agent/{agent_slug}.js")
async def get_identifiable_agent(agent_slug: str, db: Session = Depends(get_db)):
    print(f"\n[AGENT_DEBUG] 📥 에이전트 요청 수신: 원본 slug = '{agent_slug}'")
    
    if "_" not in agent_slug: 
        print("[AGENT_DEBUG] ❌ 잘못된 Slug 포맷 (언더바 없음)")
        return Response(content="console.error('Invalid Slug');", media_type="application/javascript")
        
    try:
        user_id_str, domain = agent_slug.replace(".js", "").split("_", 1)
        user_id = int(user_id_str)
        print(f"[AGENT_DEBUG] 🔍 파싱 결과 -> User ID: {user_id}, 요청 도메인: '{domain}'")
    except Exception as e: 
        print(f"[AGENT_DEBUG] ❌ User ID 파싱 실패: {str(e)}")
        return Response(content="console.error('Invalid User ID');", media_type="application/javascript")

    clean_domain = domain.strip().replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    print(f"[AGENT_DEBUG] 🧹 정제된 검색용 도메인(clean_domain): '{clean_domain}'")
    
    # 1차 검색: 유저 ID와 도메인 부분 일치 검색
    site = db.query(models.Site).filter(models.Site.user_id == user_id, models.Site.domain.contains(clean_domain)).first()
    
    # 2차 검색 (Fallback): 도메인이 안 맞으면 해당 유저의 첫 번째 사이트로 강제 매핑 시도
    if not site: 
        print(f"[AGENT_DEBUG] ⚠️ '{clean_domain}' 도메인 일치 실패, 유저({user_id})의 기본 사이트 검색 시도...")
        site = db.query(models.Site).filter(models.Site.user_id == user_id).first()
        
    if not site: 
        print(f"[AGENT_DEBUG] ❌ DB에서 유저({user_id})의 사이트를 전혀 찾을 수 없음 (Site Not Found)")
        return Response(content="console.error('Site Not Found');", media_type="application/javascript")

    print(f"[AGENT_DEBUG] ✅ 최종 매핑된 사이트 -> ID: {site.id}, Name: '{site.site_name}', DB Domain: '{site.domain}'")

    structures = db.query(models.BoardStructure).filter(models.BoardStructure.site_id == site.id).all()
    print(f"[AGENT_DEBUG] 📋 DB에서 가져온 BoardStructure(게시판 구조) 개수: {len(structures)}개")
    
    # 🚀 DOM 셀렉터를 문자열이 아닌 JSON 객체(dict) 형태로 온전히 복원하는 로직
    seen = set()
    unique_selectors = []
    
    for struct in structures:
        db_selectors = struct.get_selectors 
        if db_selectors:
            if isinstance(db_selectors, str):
                try: db_selectors = json.loads(db_selectors)
                except:
                    try: db_selectors = ast.literal_eval(db_selectors)
                    except: continue
            
            # 리스트 안에 든 객체({tag, className}) 또는 문자열 추출
            items = db_selectors if isinstance(db_selectors, list) else [db_selectors]
            for s in items:
                if isinstance(s, dict) and "tag" in s:
                    k = (s.get("tag"), s.get("className", ""))
                    if k not in seen:
                        seen.add(k)
                        unique_selectors.append({"tag": s.get("tag"), "className": s.get("className", "")})
                elif isinstance(s, str) and s.strip():
                    # 수동으로 태그 문자열이 들어왔을 경우 객체로 규격 통일
                    k = (s.strip(), "")
                    if k not in seen:
                        seen.add(k)
                        unique_selectors.append({"tag": s.strip(), "className": ""})

    js_selectors_payload = dumps(unique_selectors, ensure_ascii=False)
    print(f"[AGENT_DEBUG] 🎯 프론트로 발급할 최종 정제된 셀렉터 페이로드: {js_selectors_payload}")

    js_template = f"""
    (function() {{
        'use strict';
        const originalFetch = window.fetch;
        const originalXHROpen = XMLHttpRequest.prototype.open;
        const originalXHRSend = XMLHttpRequest.prototype.send;
        
        console.log("[SDK][INIT] '{site.site_name}' Real-time Write/Send Shield Activated (User: {user_id})");
        const textCache = new Map();

        function isIgnoreUrl(url) {{
            if (!url || typeof url !== 'string') return false;
            if (url.includes('/api/detect')) return true; 
            const ignores = ['/ping', '/heartbeat', '/telemetry', '/log', 'mock.yamyamee.me']; 
            return ignores.some(keyword => url.toLowerCase().includes(keyword));
        }}

        // 🧠 2. 데이터 스나이퍼: 진짜 사람이 쓴 '제목/내용' 텍스트만 추출
        function extractPureText(input) {{
            if (!input) return "";
            let textValues = [];
            let rawStr = typeof input !== 'string' ? JSON.stringify(input) : input;

            // 🚫 삭제, 추천, 단순 조회 등 '글쓰기'가 아닌 시스템 명령은 즉시 무시 (검사 안함)
            if (rawStr.includes('delete_comment') || rawStr.includes('delete_document') || rawStr.includes('dispBoardDelete')) {{
                return "";
            }}

            // 🧹 HTML 태그 제거 헬퍼 (<p>악플</p> -> 악플)
            function stripHtmlAndSave(html) {{
                let tmp = document.createElement("DIV");
                tmp.innerHTML = html;
                let txt = (tmp.textContent || tmp.innerText || "").trim();
                if (txt.length > 0) textValues.push(txt);
            }}

            // 🎯 타겟팅 헬퍼: 사용자가 입력하는 진짜 필드만 쏙 빼오기
            function processKeyValue(key, value) {{
                const k = key.toLowerCase();
                // 제로보드, 라이믹스, 일반 게시판에서 주로 쓰는 본문/제목 키워드
                if (['title', 'content', 'comment', 'text', 'body', 'editor_sequence'].includes(k)) {{
                    if (typeof value === 'string') stripHtmlAndSave(value);
                }}
            }}

            try {{
                // Case 1: 최신 폼 데이터 (FormData, URLSearchParams)
                if (input instanceof FormData || input instanceof URLSearchParams) {{
                    for (let [key, value] of input.entries()) processKeyValue(key, value);
                    return textValues.join(" ");
                }}

                // Case 2: URL 인코딩 폼 (a=1&b=2) -> 라이믹스/XE에서 주로 사용
                if (typeof input === 'string' && input.includes('=') && !input.startsWith('{{') && !input.startsWith('<?xml')) {{
                    const params = new URLSearchParams(input);
                    for (let [key, value] of params.entries()) processKeyValue(key, value);
                    if (textValues.length > 0) return textValues.join(" ");
                }}

                // Case 3: JSON API
                if (typeof input === 'string' && input.startsWith('{{')) {{
                    const parsed = JSON.parse(input);
                    for (let key in parsed) processKeyValue(key, parsed[key]);
                    if (textValues.length > 0) return textValues.join(" ");
                }}

                // Case 4: XMLRPC (라이믹스/XE 구형 AJAX 통신 방식)
                if (typeof input === 'string' && input.includes('<?xml')) {{
                    // 정규식으로 CDATA(본문) 또는 <string> 태그 안의 텍스트만 강제 추출
                    let matches = input.match(/<string><\!\[CDATA\[(.*?)\]\]><\/string>/g) || input.match(/<string>(.*?)<\/string>/g);
                    if (matches) {{
                        matches.forEach(m => {{
                            let clean = m.replace(/<string><\!\[CDATA\[/, '').replace(/\]\]><\/string>/, '').replace(/<string>/, '').replace(/<\/string>/, '');
                            stripHtmlAndSave(clean);
                        }});
                    }}
                    return textValues.join(" ");
                }}
            }} catch(e) {{}}

            // 여기까지 왔는데 일반 문자열인 경우 (단축URL, UUID, 영어로만 된 해시값 등은 버림)
            if (rawStr.startsWith('http') || rawStr.length < 2 || /^[a-zA-Z0-9_\-]+$/.test(rawStr)) return "";

            return rawStr;
        }}

        function hookNetwork() {{
            window.fetch = async (...args) => {{
                let [resource, config] = args;
                if (typeof resource === 'string' && isIgnoreUrl(resource)) return originalFetch(...args);
                
                const method = config ? (config.method || 'GET').toUpperCase() : 'GET';
                if (method === 'GET') return originalFetch(...args);
                
                const rawContent = (method === 'POST' || method === 'PUT') ? (config.body) : resource;
                const contentToCheck = extractPureText(rawContent);

                if (!contentToCheck) return originalFetch(...args);

                const result = await detectBadContent(contentToCheck);
                if (result && result.isInappropriate) {{ 
                    alert("🚫 Transmission blocked by security policy: " + result.reason); 
                    throw new Error("Blocked by CleanWeb Shield"); 
                }}
                return originalFetch(...args);
            }};

            XMLHttpRequest.prototype.open = function(method, url) {{
                this._url = url; 
                this._method = (method || 'GET').toUpperCase();
                return originalXHROpen.apply(this, arguments);
            }};
            
            XMLHttpRequest.prototype.send = async function(body) {{
                if (this._url && isIgnoreUrl(this._url)) return originalXHRSend.apply(this, arguments);
                if (this._method === 'GET') return originalXHRSend.apply(this, arguments);
                
                const rawContent = (this._method === 'POST' || this._method === 'PUT') ? body : this._url;
                const contentToCheck = extractPureText(rawContent);

                if (!contentToCheck) return originalXHRSend.apply(this, arguments);

                const result = await detectBadContent(contentToCheck);
                if (result && result.isInappropriate) {{ 
                    alert("🚫 Transmission blocked by security policy: " + result.reason); 
                    this.abort(); return; 
                }}
                return originalXHRSend.apply(this, arguments);
            }};
        }}

        async function detectBadContent(text) {{
            if (!text || text.trim() === "") return {{ isInappropriate: false }};
            const checkText = text.length > 2000 ? text.substring(0, 2000) : text;
            if (textCache.has(checkText)) return textCache.get(checkText);
            try {{
                const response = await originalFetch("https://api.yamyamee.me/api/detect", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ text: checkText, user_id: {site.user_id} }})
                }});
                const result = await response.json();
                textCache.set(checkText, result);
                return result;
            }} catch (err) {{ return {{ isInappropriate: false }}; }}
        }}

        function init() {{ hookNetwork(); }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
        
    }})();
    """
    
    return Response(
        content=js_template, 
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


# ==========================================
# [5] 회원가입, 로그인 및 대시보드 통계 API
# ==========================================

@app.post("/api/signup")
async def signup(payload: dict, db: Session = Depends(get_db)):
    username, password, nickname = payload.get("username"), payload.get("password"), payload.get("nickname")
    if db.query(models.User).filter(models.User.username == username).first(): return {"success": False, "message": "중복 아이디"}
    db.add(models.User(username=username, password=password, nickname=nickname))
    db.commit()
    return {"success": True}

@app.post("/api/login")
async def login(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    user = db.query(User).filter(User.username == data.get("username")).first()
    if not user or user.password != data.get("password"): return {"success": False}
    return {"success": True, "user_id": user.id, "nickname": user.nickname}

@app.post("/api/setting/update")
async def update_user_domain_setting(payload: dict, db: Session = Depends(get_db)):
    user_id, domain, preferences = payload.get("user_id"), payload.get("domain", "").strip(), payload.get("preferences")
    site = db.query(models.Site).filter(models.Site.user_id == user_id, models.Site.domain.contains(domain)).first()
    if not site: return {"success": False}
    setting = db.query(models.UserSetting).filter(models.UserSetting.site_id == site.id).first()
    if not setting:
        setting = models.UserSetting(user_id=user_id, site_id=site.id, preferences=preferences)
        db.add(setting)
    else: setting.preferences = preferences
    db.commit()
    return {"success": True}

@app.get("/api/setting/{user_id}")
async def get_user_setting(user_id: int, domain: Optional[str] = Query(None), db: Session = Depends(get_db)):
    query = db.query(UserSetting).filter(UserSetting.user_id == user_id)
    setting = query.join(Site, UserSetting.site_id == Site.id).filter(Site.domain.contains(domain.strip())).first() if domain else query.order_by(UserSetting.id.desc()).first()
    if not setting: return {"success": True, "preferences": {"banned_types": [], "is_active": True, "sensitivity": "medium", "custom_keywords": []}}
    return {"success": True, "preferences": setting.preferences}

@app.get("/api/dashboard/stats/{user_id}")
async def get_dashboard_statistics(user_id: int, db: Session = Depends(get_db)):
    total_count = db.query(DetectionLog).filter(DetectionLog.user_id == user_id).count()
    stage_stats = db.query(DetectionLog.stage, func.count(DetectionLog.id).label("count")).filter(DetectionLog.user_id == user_id).group_by(DetectionLog.stage).all()
    stage_chart = {row.stage: row.count for row in stage_stats}
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    daily_stats = db.query(func.date(DetectionLog.created_at).label("date"), func.count(DetectionLog.id).label("count")).filter(DetectionLog.user_id == user_id, DetectionLog.created_at >= seven_days_ago).group_by(func.date(DetectionLog.created_at)).all()
    trend_chart = [{"date": str(row.date), "count": row.count} for row in daily_stats]
    recent_logs = db.query(DetectionLog).filter(DetectionLog.user_id == user_id).order_by(DetectionLog.created_at.desc()).limit(5).all()
    recent_list = [{
        "id": log.id, 
        "stage": log.stage, 
        "reason": log.reason, 
        "blocked_content": log.blocked_content or "내용 없음", 
        "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S")
    } for log in recent_logs]
    return {"success": True, "summary": {"total_detected_count": total_count}, "charts": {"stage_distribution": stage_chart, "weekly_trend": trend_chart}, "recent_logs": recent_list}

@app.get("/api/analyze/check")
async def check_analysis_status(url: str = Query(..., description="대상 URL"), user_id: int = Query(...), db: Session = Depends(get_db)):
    domain = url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0].strip()
    site = db.query(models.Site).filter(models.Site.domain == domain, models.Site.user_id == user_id).first()
    if not site: return {"success": True, "is_analyzed": False}
    return {"success": True, "is_analyzed": db.query(models.BoardStructure.id).filter(models.BoardStructure.site_id == site.id).first() is not None}

@app.get("/api/sites/{user_id}")
async def get_user_registered_sites(user_id: int, db: Session = Depends(get_db)):
    user_sites = db.query(models.Site).filter(models.Site.user_id == user_id).all()
    sites_payload = [{"site_id": s.id, "site_name": s.site_name, "domain": s.domain, "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else None} for s in user_sites]
    return {"success": True, "sites": sites_payload}

@app.post("/api/detect/bulk")
async def detect_bulk_proxy(payload: BulkDetectRequest): # 🚀 BackgroundTasks 제거됨
    target_user_id = payload.user_id
    final_results = []
    valid_texts = []

    print(f"\n[DEBUG] 🚀 [Bulk] 익스텐션 대량 검사 요청 수신 | 문장 개수: {len(payload.texts)}개")
    print(f"\n[API_DEBUG] 📦 대량 검사 | 텍스트 {len(payload.texts)}개 | 수신된 Threshold: {payload.threshold}")

    # 1. 빈 텍스트 걸러내고 유효한 텍스트만 모으기
    for raw_text in payload.texts:
        cleaned_text = raw_text.strip()
        if not cleaned_text:
            continue
        
        # 서버 메모리 보호
        if len(cleaned_text) > 2000:
            cleaned_text = cleaned_text[:2000]
            
        valid_texts.append(raw_text)

    if not valid_texts:
        return []

    # 2. AI 서버의 Batch API로 딱 한 번만 전송
    async with httpx.AsyncClient() as client:
        try:
            BATCH_API_URL = INFERENCE_ENGINE_URL.rstrip("/") + "/batch"
            
            # 눈으로 진짜 주소가 잘 만들어졌는지 확인하기 위한 로그
            print(f"[DEBUG] 🌐 완성된 Batch 요청 주소: {BATCH_API_URL}")
            print(f"[DEBUG] 🧠 [AI 전송] {len(valid_texts)}개 문장 Batch API로 일괄 요청 (통신 1회)")
            
            response = await client.post(
                BATCH_API_URL, 
                json={"texts": valid_texts, "threshold": payload.threshold}, 
                timeout=30.0
            )
            
            if response.status_code == 200:
                batch_results = response.json().get("results", [])
                
                # 3. 📥 결과 매핑 (DB 로그 저장 로직 완전 삭제!)
                for raw_text, ai_result in zip(valid_texts, batch_results):
                    is_bad = ai_result.get("isInappropriate", False)
                    
                    if is_bad:
                        # 차단되었다는 사실을 터미널 로그로만 남기고, DB에는 저장하지 않습니다.
                        print(f"[DEBUG] 🛑 [차단됨] '{raw_text[:15]}...' -> {ai_result.get('reason')}")
                        # ❌ 기존에 있던 background_tasks.add_task(save_detection_log...) 코드 삭제됨
                    
                    final_results.append({
                        "text": raw_text, 
                        "isInappropriate": is_bad,
                        "reason": ai_result.get("reason") if is_bad else None
                    })
            else:
                print(f"[DEBUG] ❌ AI 서버 에러 (상태코드: {response.status_code})")
                for text in valid_texts:
                    final_results.append({"text": text, "isInappropriate": False})
                    
        except Exception as e:
            print(f"[DEBUG] ❌ 통신 예외 발생: {str(e)}")
            for text in valid_texts:
                final_results.append({"text": text, "isInappropriate": False})

    print(f"[DEBUG] ✅ [Bulk] 검사 완료 (로그 미저장) | 총 {len(final_results)}건 반환")
    return final_results

@app.post("/api/detect/stream")
async def detect_stream_proxy(payload: BulkDetectRequest):
    print(f"\n[DEBUG] 🌊 [Stream] 실시간 마이크로 배치 스트리밍 요청 수신 | 문장: {len(payload.texts)}개")
    print(f"\n[API_DEBUG] 🌊 스트림 검사 | 텍스트 {len(payload.texts)}개 | 수신된 Threshold: {payload.threshold}")

    # 1. 텍스트 전처리 (원본은 유지)
    valid_texts = []
    for raw_text in payload.texts:
        cleaned = raw_text.strip()
        if cleaned:
            valid_texts.append(raw_text)

    # 2. 🚀 데이터를 실시간으로 쏴주는 제너레이터 함수
    async def event_generator():
        if not valid_texts:
            return

        chunk_size = 20 # 💡 20개씩 묶어서 AI 서버로 보냅니다.
        
        async with httpx.AsyncClient() as client:
            BATCH_API_URL = INFERENCE_ENGINE_URL.rstrip("/") + "/batch"
            
            for i in range(0, len(valid_texts), chunk_size):
                chunk = valid_texts[i:i + chunk_size]
                
                try:
                    # AI 서버는 기존처럼 Batch로 고속 처리
                    response = await client.post(BATCH_API_URL, json={"texts": chunk, "threshold": payload.threshold}, timeout=15.0)
                    
                    if response.status_code == 200:
                        batch_results = response.json().get("results", [])
                        
                        # 📥 20개의 결과가 나오자마자 브라우저로 1개씩 즉시 발사 (SSE 규격)
                        for raw_text, ai_result in zip(chunk, batch_results):
                            is_bad = ai_result.get("isInappropriate", False)
                            
                            data_obj = {
                                "text": raw_text,
                                "isInappropriate": is_bad,
                                "reason": ai_result.get("reason") if is_bad else None
                            }
                            # "data: {JSON}\n\n" 포맷이 스트리밍(SSE)의 핵심입니다.
                            yield f"data: {json.dumps(data_obj, ensure_ascii=False)}\n\n"
                            
                except Exception as e:
                    print(f"[DEBUG] ❌ 청크 통신 예외 발생: {str(e)}")
                    # 에러가 나도 스트리밍이 끊기지 않게 안전 결과 전송
                    for text in chunk:
                        data_obj = {"text": text, "isInappropriate": False}
                        yield f"data: {json.dumps(data_obj, ensure_ascii=False)}\n\n"

    # 3. HTTP 연결을 끊지 않고 계속 데이터를 흘려보냅니다.
    return StreamingResponse(event_generator(), media_type="text/event-stream")