import os
import json
import asyncio
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
        """웹사이트의 화면 텍스트(iframe 포함) 및 입력창 구조를 캡처합니다."""
        async with async_playwright() as p:
            # 1. 봇 차단 우회를 위한 User-Agent 위장 설정
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # POST 패킷 스니핑 (수집)
            page.on("request", lambda req: self.captured_data["http_posts"].append({
                "url": req.url, "payload": req.post_data
            }) if req.method == "POST" else None)

            await page.goto(self.url)
            
            # 2. 포렌식 진단용: 봇이 보는 실제 화면 캡처
            await page.screenshot(path="debug_bot_view.png")
            print("\n📸 [진단] 봇이 접속한 화면을 'debug_bot_view.png'로 저장했습니다.")

            # 무한 대기 방지 로직
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            # 3. [핵심] 모든 프레임(iframe 포함)을 순회하며 텍스트 긁어오기
            all_contents = []
            for frame in page.frames:
                try:
                    content = await frame.evaluate("""() => {
                        const ignoreSelectors = 'nav, header, footer, aside, .gnb, .sidebar';
                        
                        const elements = Array.from(document.querySelectorAll('div, article, section, p, li, td, a'))
                            .filter(el => !el.closest(ignoreSelectors) && el.innerText && el.innerText.trim().length > 10);

                        return elements.map(el => {
                            const cls = (el.className || '').toString().toLowerCase().replace(/\s+/g, ' ').trim();
                            const id = (el.id || '').toString().toLowerCase();
                            let score = el.innerText.trim().length;

                            // 스마트 휴리스틱 (가산점 부여)
                            if (cls.includes('content') || cls.includes('comment') || cls.includes('reply') || cls.includes('body')) score += 5000;
                            if (id.includes('content') || id.includes('comment') || id.includes('reply')) score += 5000;
                            if (cls.includes('xe_content') || cls.includes('rhymix_content') || cls.includes('document_default')) score += 10000;
                            if (cls.includes('title') || cls.includes('sj')) score += 3000; // 목록 제목 우대

                            return { 
                                tag: el.tagName, 
                                id: el.id || null, 
                                className: cls || null, 
                                text: el.innerText.substring(0, 150).replace(/\\n/g, ' '),
                                score: score
                            };
                        });
                    }""")
                    all_contents.extend(content)
                except Exception:
                    continue

            # 중복 텍스트 제거 및 점수순 정렬
            unique_results = []
            seen_texts = set()
            
            for item in sorted(all_contents, key=lambda x: x['score'], reverse=True):
                if item['text'] not in seen_texts:
                    seen_texts.add(item['text'])
                    # LLM 전송 시에는 score 정보는 삭제하여 토큰 절약
                    del item['score']
                    unique_results.append(item)

            self.captured_data["dom_contents"] = unique_results[:25]

            # 4. POST 폼 샘플링 (iframe 포함 순회)
            all_inputs = []
            for frame in page.frames:
                try:
                    inputs = await frame.evaluate("""() => {
                        return Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'))
                            .map(el => ({ tag: el.tagName, id: el.id, name: el.name, placeholder: el.placeholder }));
                    }""")
                    all_inputs.extend(inputs)
                except Exception:
                    continue
            self.captured_data["dom_inputs"] = all_inputs

            await browser.close()
            
            # 5. 디버그 로그
            print("\n" + "="*50)
            print("👀 [DEBUG] LLM에게 전송될 화면 텍스트 데이터 (상위 5개)")
            print("="*50)
            for item in self.captured_data["dom_contents"][:5]:
                print(json.dumps(item, ensure_ascii=False))
            print("="*50 + "\n")

            return self.captured_data

    async def analyze_with_llm(self):
        """수집된 데이터를 OpenAI로 분석하여 정규화된 룰을 도출합니다."""
        
        # ⚠️ 중요: 프롬프트에 'JSON'이라는 단어가 반드시 포함되어야 400 에러가 나지 않습니다.
        prompt = f"""
        현재 분석 중인 URL: {self.url}
        수집된 데이터: {json.dumps(self.captured_data, ensure_ascii=False)}

        지시사항:
        1. URL 패턴 분석: 이 URL에서 계속 바뀌는 글 번호나 페이지 번호(id, document_srl, page 등)를 '*'로 치환하세요.
        
        2. GET 분석 (UGC 전체 탐색): 
           - 이 페이지에서 "일반 사용자가 직접 작성한 텍스트"가 노출되는 CSS Selector를 찾아내세요.
           - [본문/댓글 페이지]: '게시글 본문(article)'과 '댓글(comment)' 셀렉터를 찾으세요. (예: .xe_content, .comment_content)
           - [목록 페이지]: 여러 글이 나열된 게시판 목록이라면, 사용자가 작성한 '게시글 제목(list_title)' 셀렉터를 찾으세요. (예: td.title, .title)
           - 메뉴, 사이드바, 헤더, 푸터 같은 사이트 공통 디자인 요소는 절대 포함하지 마세요.
           
        3. POST 분석: 서버 전송 필드와 입력창 DOM Selector 매핑.

        응답은 반드시 아래 JSON 형식으로만 반환하세요:
        {{
            "url_pattern": "예: zeropage.org/hello*",
            "structural_params": ["page", "document_srl"],
            "get_rules": [
                {{ "type": "article", "selector": "본문_셀렉터 (있을 경우만)" }},
                {{ "type": "comment", "selector": "댓글_셀렉터 (있을 경우만)" }},
                {{ "type": "list_title", "selector": "목록_제목_셀렉터 (목록 페이지일 경우)" }}
            ],
            "post_rules": {{
                "endpoint": "API_주소",
                "mapping": [
                    {{ "field": "전송필드명", "selector": "입력창셀렉터" }}
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
        """LLM이 도출한 Selector가 실제 화면(iframe 포함)에 존재하는지 검증합니다."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0")
            page = await context.new_page()
            await page.goto(self.url)
            
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except: pass
            
            verified_get = []
            for rule in get_rules:
                is_found = False
                # 메인 페이지 및 모든 iframe을 샅샅이 뒤져서 검증
                for frame in page.frames:
                    try:
                        element = await frame.wait_for_selector(rule['selector'], timeout=3000)
                        if element and await element.is_visible():
                            content = await element.inner_text()
                            if len(content.strip()) > 0:
                                is_found = True
                                break
                    except Exception:
                        continue
                
                if is_found:
                    verified_get.append(rule)
                else:
                    print(f"⚠️ [검증 실패] {rule['selector']} 요소에서 텍스트를 찾지 못했습니다.")
                    
            await browser.close()
            return verified_get