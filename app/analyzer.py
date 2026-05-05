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
        # dom_inputs 제거, 패킷(http_posts)과 텍스트(dom_contents)만 수집
        self.captured_data = {"dom_contents": [], "http_posts": []}

    async def capture_all(self):
        """웹사이트의 화면 텍스트 캡처 및 POST 패킷 스니핑을 수행합니다."""
        debug_dir = "debug_output"
        os.makedirs(debug_dir, exist_ok=True)
        
        async with async_playwright() as p:
            # 💡 [팁] 나중에 직접 브라우저에서 글을 써서 패킷을 잡으려면 headless=False 로 변경하세요.
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # 🚨 [POST 스니핑] 네트워크 탭에서 POST 요청만 가로채서 저장 (DOM 구조 신경 안 씀)
            page.on("request", lambda req: self.captured_data["http_posts"].append({
                "url": req.url, 
                "payload": req.post_data
            }) if req.method == "POST" else None)

            await page.goto(self.url)
            await page.screenshot(path=f"{debug_dir}/bot_view.png")
            
            try:
                # 패킷을 잡기 위해 충분히 대기
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            # ---------------------------------------------------------
            # 1. 화면의 모든 유효 텍스트 긁어오기 (TreeWalker)
            # ---------------------------------------------------------
            all_contents = []
            for frame in page.frames:
                try:
                    content = await frame.evaluate("""() => {
                        const ignoreSelectors = 'nav, header, footer, aside, script, style, noscript, svg, .gnb, .sidebar, .menu, .pagination';
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                        let node;
                        const results = [];

                        while (node = walker.nextNode()) {
                            let text = node.nodeValue.trim();
                            if (text.length < 5) continue;

                            let targetEl = node.parentElement;
                            if (!targetEl || targetEl.closest(ignoreSelectors) || targetEl.offsetParent === null) continue;

                            let found = false;
                            while (targetEl && targetEl !== document.body) {
                                let cls = (targetEl.className || '').toString().trim();
                                let id = targetEl.id || '';
                                if (cls || id) {
                                    found = true;
                                    break;
                                }
                                targetEl = targetEl.parentElement;
                            }

                            if (!found) continue;

                            let cls = (targetEl.className || '').toString().toLowerCase().replace(/\\s+/g, ' ').trim();
                            let id = (targetEl.id || '').toString().toLowerCase();
                            let tag = targetEl.tagName.toLowerCase();
                            
                            let fullText = targetEl.innerText ? targetEl.innerText.trim() : text;
                            if (fullText.length < 5) continue;

                            let score = fullText.length;
                            
                            if (cls.includes('title') || cls.includes('subject') || cls.includes('list') || cls.includes('item')) score += 3000;
                            if (cls.includes('content') || cls.includes('article') || cls.includes('body')) score += 4000;
                            if (cls.includes('xe_content') || cls.includes('bo_v_con')) score += 5000;

                            results.push({
                                tag: tag,
                                id: id || null,
                                className: cls || null,
                                text: fullText.substring(0, 200).replace(/\\n/g, ' '),
                                score: score
                            });
                        }
                        return results;
                    }""")
                    all_contents.extend(content)
                except Exception:
                    continue

            # ---------------------------------------------------------
            # 2. 파이썬 단에서 데이터 2분할 (GET 분석용)
            # ---------------------------------------------------------
            full_extraction = []
            seen_full_texts = set()
            
            for item in sorted(all_contents, key=lambda x: x['score'], reverse=True):
                if item['text'] not in seen_full_texts:
                    seen_full_texts.add(item['text'])
                    full_extraction.append(item.copy())

            pattern_samples = []
            seen_patterns = set()
            
            for item in full_extraction:
                cls_first = item['className'].split(' ')[0] if item['className'] else ''
                pattern_key = f"{item['tag']}#{item['id']}.{cls_first}"
                
                if pattern_key not in seen_patterns:
                    seen_patterns.add(pattern_key)
                    llm_item = item.copy()
                    del llm_item['score']
                    pattern_samples.append(llm_item)

            self.captured_data["dom_contents"] = pattern_samples[:40]

            # ---------------------------------------------------------
            # 3. 디버그 파일 저장 (DOM Input 로직 완전히 삭제됨)
            # ---------------------------------------------------------
            with open(f"{debug_dir}/full_extraction.json", "w", encoding="utf-8") as f:
                json.dump(full_extraction, f, ensure_ascii=False, indent=2)
                
            with open(f"{debug_dir}/pattern_samples.json", "w", encoding="utf-8") as f:
                json.dump(pattern_samples, f, ensure_ascii=False, indent=2)

            # 수집된 POST 패킷만 깔끔하게 저장
            with open(f"{debug_dir}/post_packets.json", "w", encoding="utf-8") as f:
                json.dump(self.captured_data["http_posts"], f, ensure_ascii=False, indent=2)

            print(f"\n📁 [디버그 저장 완료] '{debug_dir}/' 폴더 안에 파일들이 저장되었습니다.")
            print(f" ├─ 스크린샷: bot_view.png")
            print(f" ├─ GET 구조 샘플: pattern_samples.json ({len(pattern_samples)}개)")
            print(f" └─ POST 패킷 기록: post_packets.json ({len(self.captured_data['http_posts'])}개 발견)\n")

            await browser.close()
            return self.captured_data

    async def analyze_with_llm(self):
        """수집된 데이터를 OpenAI로 분석하여 정규화된 룰을 도출합니다."""
        
        prompt = f"""
        현재 분석 중인 URL: {self.url}
        수집된 데이터: {json.dumps(self.captured_data, ensure_ascii=False)}

        지시사항:
        1. URL 패턴 분석: 이 URL에서 계속 바뀌는 글 번호나 페이지 번호(id, document_srl, page 등)를 '*'로 치환하세요.
        
        2. GET 분석 (UGC 전체 탐색): 
           - 이 페이지에서 "일반 사용자가 직접 작성한 텍스트"가 노출되는 CSS Selector를 찾아내세요.
           - [본문/댓글 페이지]: '게시글 본문(article)'과 '댓글(comment)' 셀렉터를 찾으세요.
           - [목록 페이지]: 여러 글이 나열된 게시판 목록이라면, 사용자가 작성한 '게시글 제목(list_title)' 셀렉터를 찾으세요.
           
        3. POST 분석 (패킷 스니핑 데이터 기반):
           - 수집된 http_posts 데이터를 바탕으로, 이 사이트에서 데이터를 전송할 때 사용하는 엔드포인트(URL)와 전송되는 필드명(Payload keys)만 추출하세요. (DOM 구조 맵핑 불필요)

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
                "endpoint": "API_주소 (발견된 POST 요청이 있을 경우)",
                "payload_fields": ["전송필드명1", "전송필드명2"]
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
        # 검증 로직은 GET Selector 위주이므로 기존 코드 그대로 유지
        # ... (이전 코드 동일)
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