import os
import json
from playwright.async_api import async_playwright
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

class SiteAnalyzer:
    def __init__(self, url):
        self.url = url
        self.captured_data = {"dom_inputs": [], "dom_contents": [], "http_posts": []}

    async def capture_all(self):
        """웹사이트의 GET/POST 관련 구조를 캡처합니다."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # POST 패킷 스니핑
            page.on("request", lambda req: self.captured_data["http_posts"].append({
                "url": req.url, "payload": req.post_data
            }) if req.method == "POST" else None)

            await page.goto(self.url)
            await page.wait_for_load_state("networkidle")

            # GET용 텍스트 컨텐츠 샘플링 (본문, 댓글 영역 파악용)
            self.captured_data["dom_contents"] = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('div, article, section, p, td, li'))
                    .filter(el => el.innerText && el.innerText.trim().length > 30)
                    .map(el => ({ tag: el.tagName, id: el.id, className: el.className, text: el.innerText.substring(0, 100).replace(/\\n/g, ' ') }))
                    .slice(0, 15);
            }""")

            # POST용 입력창 폼 샘플링
            self.captured_data["dom_inputs"] = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'))
                    .map(el => ({ tag: el.tagName, id: el.id, name: el.name, placeholder: el.placeholder }));
            }""")

            await browser.close()
            return self.captured_data

    async def analyze_with_llm(self):
        """수집된 데이터를 OpenAI로 분석하여 정규화된 룰을 도출합니다."""
        prompt = f"""
        현재 분석 중인 URL: {self.url}
        수집된 데이터: {json.dumps(self.captured_data, ensure_ascii=False)}

        지시사항:
        1. URL 패턴 분석: 이 URL에서 'id'나 'page' 같이 값이 계속 바뀌는 파라미터를 식별하고, 이를 '*'로 치환한 대표 패턴(url_pattern)을 만드세요.
        2. GET 분석: 사용자의 게시글 본문이나 댓글이 출력되는 CSS Selector를 찾으세요. (가장 정확한 1~2개만)
        3. POST 분석: 서버 전송 필드와 입력창 DOM Selector 매핑.

        응답은 반드시 아래 JSON 형식으로만 하세요:
        {{
            "url_pattern": "예: gall.dcinside.com/board/view/?id=*&no=*",
            "structural_params": ["핵심파라미터1", "핵심파라미터2"],
            "get_rules": [
                {{ "type": "article", "selector": "본문셀렉터" }},
                {{ "type": "comment", "selector": "댓글셀렉터" }}
            ],
            "post_rules": {{
                "endpoint": "API_주소",
                "mapping": [
                    {{ "field": "서버전송필드명", "selector": "입력창셀렉터" }}
                ]
            }}
        }}
        """
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)

    async def verify_analysis(self, get_rules):
        """LLM이 도출한 GET Selector가 실제 화면에 텍스트를 담고 있는지 검증합니다."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(self.url)
            await page.wait_for_load_state("networkidle")
            
            verified_get = []
            for rule in get_rules:
                try:
                    # 셀렉터가 화면에 나타날 때까지 대기
                    element = await page.wait_for_selector(rule['selector'], timeout=3000)
                    if element and await element.is_visible():
                        content = await element.inner_text()
                        if len(content.strip()) > 0:
                            verified_get.append(rule)
                except Exception as e:
                    print(f"검증 실패 ({rule['selector']}): {e}")
                    continue
                    
            await browser.close()
            return verified_get