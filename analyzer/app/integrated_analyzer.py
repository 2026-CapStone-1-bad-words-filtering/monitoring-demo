import os
import json
import asyncio
import random
import re
import time
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright

class IntegratedAnalyzer:
    def __init__(self, start_url, log_queue=None, max_pages=20, delay_range=(1.0, 2.0)):
        self.start_url = start_url
        self.domain = urlparse(start_url).netloc
        self.log_queue = log_queue  # 실시간 로그 전달용 큐
        self.max_pages = max_pages
        self.auth_file = os.path.join("auth", "login_state.json")
        self.delay_range = delay_range
        
        # 가중치 기반 키워드 (글쓰기/등록 버튼 탐색용)
        self.write_keywords = {"글쓰기", "쓰기", "작성", "Write", "New Post"}
        self.submit_keywords = {"등록", "확인", "완료", "저장", "Submit", "Publish"}
        
        # 상태 관리
        self.visited_urls = set()
        self.analyzed_mids = set()
        self.queue = [start_url]
        self.final_structures = {}

    def debug(self, category, message):
        """필요한 로그만 큐에 넣고 콘솔에 출력합니다."""
        now = time.strftime("%H:%M:%S")
        log_msg = f"[{now}] [{category}] {message}"
        print(log_msg)
        if self.log_queue:
            # 비동기 큐에 로그 삽입
            self.log_queue.put_nowait(log_msg)

    def extract_mid(self, url):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if 'mid' in qs:
            return qs['mid'][0]
        path_parts = parsed.path.strip('/').split('/')
        return path_parts[0] if path_parts and path_parts[0] else "main"

    async def run(self):
        """실제 크롤링 및 분석을 수행하는 코어 로직"""
        async with async_playwright() as p:
            self.debug("SYSTEM", f"분석 엔진 가동 (대상: {self.domain})")

            # Docker 환경을 위해 headless=True 설정
            browser = await p.chromium.launch(headless=True)
            
            # 세션 로드 체크
            storage_state = self.auth_file if os.path.exists(self.auth_file) else None
            context = await browser.new_context(storage_state=storage_state)
            if storage_state:
                self.debug("AUTH", "로그인 세션을 성공적으로 로드했습니다.")
            
            page = await context.new_page()

            # POST 패킷 인터셉트 설정
            async def intercept_route(route):
                request = route.request
                if request.method == "POST":
                    curr_mid = self.extract_mid(page.url)
                    data = request.post_data or ""
                    
                    if curr_mid not in self.final_structures:
                        self.final_structures[curr_mid] = {"get_selectors": [], "post_metadata": {}}
                    
                    self.final_structures[curr_mid]["post_metadata"] = {
                        "endpoint": request.url,
                        "fields": list(parse_qs(data).keys()) if data else [],
                        "payload_sample": data[:1000]
                    }
                    self.debug("POST_CAPTURE", f"[{curr_mid}] 게시글 전송 패턴 획득 성공")
                    await route.abort() # 실제 전송은 차단
                else:
                    await route.continue_()

            await page.route("**/*", intercept_route)

            # 크롤링 및 분석 루프
            while self.queue and len(self.visited_urls) < self.max_pages:
                current_url = self.queue.pop(0)
                if current_url in self.visited_urls:
                    continue

                self.visited_urls.add(current_url)
                mid = self.extract_mid(current_url)

                try:
                    await asyncio.sleep(random.uniform(*self.delay_range))
                    self.debug("CRAWL", f"방문 중: {current_url} (남은 작업: {len(self.queue)})")
                    
                    response = await page.goto(current_url, wait_until="networkidle", timeout=15000)
                    if not response or response.status >= 400:
                        continue

                    # 새로운 게시판(mid) 발견 시 구조 분석
                    if mid not in self.analyzed_mids:
                        self.debug("ANALYSIS", f"[{mid}] 게시판 구조 추출 시작...")
                        get_rules = await self.analyze_get_structure(page)
                        
                        if mid not in self.final_structures:
                            self.final_structures[mid] = {"get_selectors": [], "post_metadata": {}}
                        
                        self.final_structures[mid]["get_selectors"] = get_rules
                        self.final_structures[mid]["url_pattern"] = current_url
                        
                        # 메인 페이지가 아니면 글쓰기 시도 (POST 캡처용)
                        if mid != "main":
                            await self.try_post_capture(page, mid)
                        
                        self.analyzed_mids.add(mid)

                    # 다음 링크 수집
                    await self.collect_links(page)

                except Exception as e:
                    self.debug("ERROR", f"{current_url} 분석 중 오류: {str(e)}")

            await browser.close()
            self.debug("SYSTEM", "모든 분석 공정이 완료되었습니다.")
            
            # 최종 결과 데이터를 특수 태그와 함께 로그로 보냄 (api-server 가로채기용)
            self.debug("RESULT_DATA", json.dumps(self.final_structures))
            return self.final_structures

    async def analyze_get_structure(self, page):
        """게시글 본문이 담긴 것으로 추정되는 태그들을 점수제로 추출합니다."""
        return await page.evaluate("""() => {
            const ignoreSelectors = 'nav, header, footer, aside, script, style, .pagination';
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            let node;
            const results = [];
            const seenTexts = new Set();

            while (node = walker.nextNode()) {
                let text = node.nodeValue.trim();
                if (text.length < 10) continue;

                let container = node.parentElement;
                if (!container || container.closest(ignoreSelectors) || container.offsetParent === null) continue;

                while (container && container !== document.body && !container.className && !container.id) {
                    container = container.parentElement;
                }

                let cls = (container.className || "").toString().trim();
                let fullText = (container.innerText || text).replace(/\s+/g, ' ').trim();

                if (seenTexts.has(fullText)) continue;
                seenTexts.add(fullText);

                let score = fullText.length;
                const lowerCls = cls.toLowerCase();
                if (lowerCls.includes('title') || lowerCls.includes('subject')) score += 3000;
                if (lowerCls.includes('content') || lowerCls.includes('article')) score += 5000;

                results.push({
                    tag: container.tagName.toLowerCase(),
                    className: cls,
                    score: score
                });
            }
            return results.sort((a, b) => b.score - a.score).slice(0, 10);
        }""")

    async def try_post_capture(self, page, mid):
        """가상으로 글쓰기 버튼을 눌러 POST 패킷을 유도합니다."""
        try:
            write_regex = "|".join(self.write_keywords)
            write_btn = page.locator('a, button').filter(has_text=re.compile(f"^({write_regex})$", re.I)).first
            
            if await write_btn.is_visible(timeout=2000):
                await write_btn.click()
                await page.wait_for_load_state("networkidle")
                
                # 가짜 데이터 입력
                await page.evaluate("""() => {
                    document.querySelectorAll('input[type="text"], textarea, [contenteditable="true"]')
                            .forEach(f => { f.value = "SAMPLE_DATA"; f.innerHTML = "SAMPLE_DATA"; });
                }""")
                
                submit_regex = "|".join(self.submit_keywords)
                submit_btn = page.locator('button, input[type="submit"]').filter(has_text=re.compile(f"^({submit_regex})$", re.I)).first
                
                if await submit_btn.is_visible(timeout=2000):
                    await submit_btn.click(no_wait_after=True)
                    await asyncio.sleep(1.0)
        except:
            pass

    async def collect_links(self, page):
        links = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(el => el.href)")
        for link in links:
            if link.startswith(self.start_url) and link not in self.visited_urls and link not in self.queue:
                self.queue.append(link)

    # =====================================================================
    # [추가된 로직] main.py와 통신하기 위한 브릿지 (스트리밍 제너레이터)
    # =====================================================================
    async def analyze(self):
        """
        API 서버(main.py)의 StreamingResponse에 맞춰
        run()을 백그라운드에서 돌리면서 log_queue의 내용을 실시간으로 yield 합니다.
        """
        # 스트리밍을 위한 큐 초기화
        self.log_queue = asyncio.Queue()
        
        # 메인 분석 엔진(run)을 백그라운드 태스크로 실행
        task = asyncio.create_task(self.run())
        
        # 태스크가 살아있거나 큐에 남은 로그가 있을 때까지 반복
        while not task.done() or not self.log_queue.empty():
            try:
                # 0.1초마다 큐에서 메시지를 빼서 스트리밍 (없으면 Timeout 발생)
                msg = await asyncio.wait_for(self.log_queue.get(), timeout=0.1)
                yield msg
            except asyncio.TimeoutError:
                # 큐가 비어있으면 다음 틱으로 넘김
                continue
            except Exception as e:
                yield f"[ERROR] 스트리밍 중 브릿지 오류 발생: {str(e)}"
                break