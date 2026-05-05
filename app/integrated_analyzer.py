import os
import json
import asyncio
import random
import re
import time
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright
# SQLAlchemy 모델은 정의되어 있다고 가정 (Site, BoardStructure)

class DebugIntegratedAnalyzer:
    def __init__(self, start_url, db_session=None, max_pages=30):
        self.start_url = start_url
        self.domain = urlparse(start_url).netloc
        self.max_pages = max_pages
        self.db_session = db_session
        self.auth_file = os.path.join("auth", "login_state.json")
        
        # 가중치 기반 키워드
        self.write_keywords = {"글쓰기", "쓰기", "작성", "Write", "New Post"}
        self.submit_keywords = {"등록", "확인", "완료", "저장", "Submit", "Publish"}
        
        # 상태 관리
        self.visited_urls = set()
        self.analyzed_mids = set()
        self.queue = [start_url]
        
        # 결과 데이터 구조 (DB BoardStructure 모델과 매칭)
        self.final_structures = {} # { mid: { get_rules: {}, post_rules: {} } }

    def extract_mid(self, url):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if 'mid' in qs: return qs['mid'][0]
        path_parts = parsed.path.strip('/').split('/')
        return path_parts[0] if path_parts and path_parts[0] else "main"

    async def run(self):
        async with async_playwright() as p:
            print(f"\n{'='*60}\n🚀 [SYSTEM] 통합 분석 엔진 가동 ({self.domain})\n{'='*60}")
            browser = await p.chromium.launch(headless=False) # 디버그를 위해 브라우저 띄움
            context = await browser.new_context(storage_state=self.auth_file if os.path.exists(self.auth_file) else None)
            page = await context.new_page()

            # --- [DEBUG] 네트워크 인터셉트: POST 패킷 가로채기 ---
            async def intercept_route(route):
                if route.request.method == "POST":
                    curr_mid = self.extract_mid(page.url)
                    data = route.request.post_data or ""
                    
                    print(f"  └─ 🎯 [PACKET CAPTURED] mid: {curr_mid}")
                    print(f"     [ENDPOINT] {route.request.url[:60]}...")
                    
                    if curr_mid not in self.final_structures:
                        self.final_structures[curr_mid] = {"get_selectors": {}, "post_metadata": {}}
                    
                    self.final_structures[curr_mid]["post_metadata"] = {
                        "endpoint": route.request.url,
                        "payload_sample": data[:200], # 샘플 데이터
                        "fields": list(parse_qs(data).keys()) if data else []
                    }
                    await route.abort()
                else:
                    await route.continue_()
            
            await page.route("**/*", intercept_route)

            while self.queue and len(self.visited_urls) < self.max_pages:
                current_url = self.queue.pop(0)
                if current_url in self.visited_urls: continue
                self.visited_urls.add(current_url)

                mid = self.extract_mid(current_url)
                print(f"\n🌐 [탐색] {current_url} | mid: {mid}")

                try:
                    delay = random.uniform(1.0, 1.5)
                    print(f"  [WAIT] {delay:.2f}초 대기 중...")
                    await asyncio.sleep(delay)
                    t_start = time.time()
                    await page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
                    
                    # 1. GET 구조 분석 (TreeWalker 로직 통합)
                    if mid not in self.analyzed_mids:
                        print(f"  [DEBUG] 🔍 '{mid}' 게시판 최초 발견. 구조 분석 시작...")
                        get_rules = await self.analyze_get_structure(page)
                        
                        if mid not in self.final_structures:
                            self.final_structures[mid] = {"get_selectors": {}, "post_metadata": {}}
                        self.final_structures[mid]["get_selectors"] = get_rules
                        self.final_structures[mid]["url_pattern"] = current_url

                        # 2. POST 패킷 유도 (글쓰기 -> 등록 시뮬레이션)
                        if mid != "main":
                            await self.try_post_capture(page, mid)
                        
                        self.analyzed_mids.add(mid)
                        print(f"  [DEBUG] ✅ '{mid}' 분석 완료 ({time.time() - t_start:.2f}s)")

                    # 3. 링크 수집
                    await self.collect_links(page)

                except Exception as e:
                    print(f"  [ERROR] ❌ 스킵 ({str(e)[:50]})")

            await browser.close()
            self.print_summary()
            return self.final_structures

    async def analyze_get_structure(self, page):
        return await page.evaluate("""() => {
            const ignoreSelectors = 'nav, header, footer, aside, script, style, noscript, svg, .gnb, .sidebar, .menu, .pagination';
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            let node;
            const seenTexts = new Set(); // 중복 텍스트 체크용
            const results = [];

            // HTML 엔티티 디코딩 함수 (예: &#03... 처리)
            const decodeHTMLEntities = (text) => {
                const textArea = document.createElement('textarea');
                textArea.innerHTML = text;
                return textArea.value;
            };

            while (node = walker.nextNode()) {
                let text = node.nodeValue.trim();
                
                // 1. 기초 필터링: 5자 미만 또는 숫자/날짜로만 구성된 경우 제외
                if (text.length < 5 || /^[0-9\\-. :]+$/.test(text)) continue;

                let container = node.parentElement;
                if (!container || container.closest(ignoreSelectors) || container.offsetParent === null) continue;

                // 2. 의미 있는 부모 컨테이너 찾기
                while (container && container !== document.body && !container.className && !container.id) {
                    container = container.parentElement;
                }

                let cls = (container.className || "").toString().trim();
                let fullText = decodeHTMLEntities(container.innerText || text)
                                .replace(/\\s+/g, ' ') // 공백 정규화
                                .trim();

                // 3. 중복 제거: 이미 수집된 동일 텍스트는 스킵
                if (seenTexts.has(fullText)) continue;
                seenTexts.add(fullText);

                // 4. 점수 계산 (기본 가중치 유지)
                let score = fullText.length;
                const lowerCls = cls.toLowerCase();
                if (lowerCls.includes('title') || lowerCls.includes('subject')) score += 3000;
                if (lowerCls.includes('content') || lowerCls.includes('article') || lowerCls.includes('xe_content')) score += 5000;

                results.push({
                    tag: container.tagName.toLowerCase(),
                    className: cls,
                    text: fullText.substring(0, 500), // 너무 긴 텍스트는 500자로 제한
                    score: score
                });
            }

            // 점수 순 정렬 후 상위 결과 반환
            return results.sort((a, b) => b.score - a.score).slice(0, 40);
        }""")

    async def try_post_capture(self, page, mid):
        """글쓰기 버튼을 눌러 실제 전송 패킷을 발생시킴 (디버그 로그 포함)"""
        try:
            write_regex = "|".join(self.write_keywords)
            write_btn = page.locator('a, button').filter(has_text=re.compile(f"^({write_regex})$", re.I)).first
            
            if await write_btn.is_visible(timeout=2000):
                print(f"  [DEBUG]   └─ '글쓰기' 버튼 발견. 클릭 중...")
                await write_btn.click()
                await page.wait_for_load_state("domcontentloaded")
                
                # 폼 입력
                await page.evaluate("""() => {
                    document.querySelectorAll('input[type="text"], textarea, [contenteditable="true"]').forEach(f => {
                        f.value = "DEBUG_CONTENT"; f.innerHTML = "DEBUG_CONTENT";
                    });
                }""")
                
                # 등록 버튼
                submit_regex = "|".join(self.submit_keywords)
                submit_btn = page.locator('button, input[type="submit"]').filter(has_text=re.compile(f"^({submit_regex})$", re.I)).first
                if await submit_btn.is_visible(timeout=2000):
                    print(f"  [DEBUG]   └─ '등록' 버튼 클릭. 패킷 대기...")
                    await submit_btn.click(no_wait_after=True)
                    await asyncio.sleep(1.5)
            return True
        except: return False

    async def collect_links(self, page):
        links = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(el => el.href)")
        for link in links:
            if link.startswith(self.start_url) and link not in self.visited_urls:
                self.queue.append(link)

    def print_summary(self):
        print(f"\n{'='*60}\n📊 [SUMMARY] 사이트 구조 분석 리포트\n{'='*60}")
        for mid, data in self.final_structures.items():
            print(f" 게시판 ID: {mid}")
            print(f"  ├─ GET 셀렉터: {data.get('get_selectors')}")
            print(f"  └─ POST 엔드포인트: {data.get('post_metadata', {}).get('endpoint', 'N/A')}")
        print(f"{'='*60}\n")