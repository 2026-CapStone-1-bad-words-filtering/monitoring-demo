import os
import json
import asyncio
from dotenv import load_dotenv # 1. dotenv 불러오기
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, Response
from playwright.async_api import async_playwright
import google.generativeai as genai
import uvicorn

# 2. .env 파일의 변수들을 환경 변수로 로드
load_dotenv()

app = FastAPI()

# 3. 환경 변수에서 키 가져오기
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_KEY:
    print("❌ 에러: .env 파일에 GEMINI_API_KEY가 설정되지 않았습니다.")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# 2. 상태 관리 (분석 중인 사이트 중복 실행 방지)
PENDING_ANALYSIS = set()
SITE_CONFIGS = {}

# --- [Gemini 에이전트 분석 로직] ---
async def run_analysis_agent(site_id: str, url: str):
    if site_id in PENDING_ANALYSIS:
        return
    
    PENDING_ANALYSIS.add(site_id)
    print(f"🕵️ [Agent] {site_id} 분석 시작 (Target: {url})...")
    
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            # 로컬 서버 접속 시 타임아웃 넉넉히 설정
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # HTML 스냅샷 추출 (Gemini는 문맥 파악을 잘하므로 속성 위주로 추출)
            compact_dom = await page.evaluate("""() => {
                const elements = document.querySelectorAll('input, textarea, button, form');
                return Array.from(elements).map(el => ({
                    tag: el.tagName,
                    id: el.id,
                    cls: el.className,
                    name: el.name,
                    ph: el.placeholder || '',
                    txt: (el.innerText || el.value || '').substring(0, 10)
                }));
            }""")

            # Gemini에게 분석 요청
            prompt = f"""
            웹 자동화 전문가로서, 아래 HTML 요소 목록에서 '게시글/댓글 입력창'과 '전송 버튼'의 CSS Selector를 찾아줘.
            JSON 형식으로만 답해. 예: {{"input_selector": "#id", "button_selector": ".class", "form_selector": "#form"}}
            
            [HTML Data]
            {json.dumps(compact_dom)}
            """
            
            # Gemini 1.5 Flash 호출 (JSON 모드 지원)
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            
            analysis = json.loads(response.text)
            
            # 분석 결과 저장
            SITE_CONFIGS[site_id] = {
                "input_selector": analysis.get("input_selector"),
                "button_selector": analysis.get("button_selector"),
                "form_selector": analysis.get("form_selector") or "#write_form"
            }
            print(f"✅ [Agent] 분석 완료: {SITE_CONFIGS[site_id]}")

    except Exception as e:
        print(f"❌ [Agent] 에러 발생: {str(e)}")
    finally:
        if browser:
            await browser.close()
        # 분석 목록에서 제거 (나중에 다시 접속하면 재분석 시도 가능)
        if site_id in PENDING_ANALYSIS:
            PENDING_ANALYSIS.remove(site_id)

# --- [API 엔드포인트] ---

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/loader.js")
async def get_loader(site_id: str, request: Request, background_tasks: BackgroundTasks):
    config = SITE_CONFIGS.get(site_id)
    
    # 분석 결과가 없으면 분석 시작
    if not config:
        if site_id not in PENDING_ANALYSIS:
            origin_url = request.headers.get("referer") or "http://localhost:8000"
            background_tasks.add_task(run_analysis_agent, site_id, origin_url)
        
        # 분석 중일 때 내려줄 임시 코드 (UnboundLocalError 해결)
        return Response(
            content="console.log('[ShieldAgent] 사이트 분석 중...');", 
            media_type="application/javascript"
        )

    # 분석 결과가 있을 때의 JS 주입 코드
    js_code = f"""
    (function() {{
        const CONFIG = {json.dumps(config)};
        console.log("[ShieldAgent] 설정 정보:", CONFIG);
        
        const btn = document.querySelector(CONFIG.button_selector);
        const input = document.querySelector(CONFIG.input_selector);

        if (!btn) {{
            console.error("[ShieldAgent] ❌ 버튼을 찾지 못했습니다. 셀렉터를 확인하세요:", CONFIG.button_selector);
            return;
        }}
        
        console.log("[ShieldAgent] ✅ 버튼 바인딩 성공:", btn);

        // onclick 대신 addEventListener를 써서 충돌 방지
        btn.addEventListener('click', async function(e) {{
            console.log("[ShieldAgent] 🖱️ 버튼 클릭 감지됨!");
            e.preventDefault(); 
            e.stopPropagation(); // 다른 이벤트 전파 차단

            const content = input ? input.value : "";
            console.log("[ShieldAgent] 🔍 검사 데이터:", content);

            try {{
                const res = await fetch('/filter', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ text: content, site_id: '{site_id}' }})
                }});
                
                const result = await res.json();
                console.log("[ShieldAgent] 🛡️ 필터 결과:", result);

                if (result.is_clean) {{
                    alert("✅ 필터 통과! 등록을 진행합니다.");
                    // 실제 등록 로직 (테스트 시에는 아래 주석 해제)
                    // document.querySelector(CONFIG.form_selector).submit();
                }} else {{
                    alert("❌ 차단: " + result.reason);
                }}
            }} catch (err) {{
                console.error("[ShieldAgent] 🚨 필터링 통신 에러:", err);
            }}
        }});
    }})();
    """

    return Response(content=js_code, media_type="application/javascript")

@app.post("/filter")
async def filter_api(request: Request):
    data = await request.json()
    text = data.get("text", "")
    bad_words = ["바보", "멍청이", "쓰레기"]
    found = [w for w in bad_words if w in text]

    if found:
        print(f"🚨 [DETECTION] 사이트: {data['site_id']} | 차단: {found}")
        return {"is_clean": False, "reason": "부적절한 표현 포함"}
    return {"is_clean": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)