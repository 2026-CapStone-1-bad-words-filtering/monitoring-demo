import os
import json
import asyncio
import time
from urllib.parse import urlparse
from playwright.async_api import async_playwright

# ==========================================
# 1. 환경 설정 (Config) - .env 연동
# ==========================================
class Config:
    TARGET_URL = os.getenv("TARGET_URL", "http://host.docker.internal:3000")
    TARGET_DOMAIN = urlparse(TARGET_URL).netloc
    
    MAX_PAGES = int(os.getenv("MAX_PAGES", "20"))
    HYDRATION_DELAY = float(os.getenv("HYDRATION_DELAY", "2.5"))
    USE_NGROK_BYPASS = os.getenv("USE_NGROK_BYPASS", "false").lower() == "true"

# ==========================================
# 2. DOM Parser (분석기: 계층형 구조 및 댓글 추출 탑재)
# ==========================================
class DOMParser:
    @staticmethod
    async def extract_main_content(page):
        """
        클래스 없는 태그는 부모 계층을 추적(td > a)하고, 
        댓글(짧은 텍스트)도 놓치지 않도록 스캔 로직을 대폭 강화했습니다.
        """
        return await page.evaluate("""() => {
            const ignoreSelectors = 'nav, header, footer, aside, script, style, .pagination, [role="navigation"]';
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            let node;
            const results = [];
            const seenSelectors = new Set();

            // 🚀 핵심: 클래스 없는 태그를 구출하는 계층형 셀렉터 추론기
            function getSmartSelector(el) {
                if (!el || el === document.body || el === document.documentElement) return null;
                let tag = el.tagName.toLowerCase();
                let rawCls = (typeof el.className === 'string' ? el.className : "");
                let cls = rawCls.split(/\\s+/).filter(c => c && !/\\d/.test(c)).join('.');

                // 1. 본인에게 클래스가 있으면 단독 사용
                if (cls) return { query: `${tag}.${cls}` };

                // 2. 클래스가 없으면 부모 태그를 확인
                let parent = el.parentElement;
                if (parent && parent !== document.body) {
                    let pTag = parent.tagName.toLowerCase();
                    let pRawCls = (typeof parent.className === 'string' ? parent.className : "");
                    let pCls = pRawCls.split(/\\s+/).filter(c => c && !/\\d/.test(c)).join('.');

                    if (pCls) {
                        return { query: `${pTag}.${pCls} > ${tag}` }; // 예: td.text-center > a
                    } else {
                        return { query: `${pTag} > ${tag}` }; // 예: tr > td
                    }
                }
                return { query: tag };
            }

            while (node = walker.nextNode()) {
                let text = node.nodeValue.trim();
                // 🚀 댓글은 짧을 수 있으므로 제한을 3글자로 대폭 낮춤
                if (text.length < 3) continue; 

                let container = node.parentElement;
                if (!container || container.closest(ignoreSelectors) || container.offsetParent === null) continue;

                let selObj = getSmartSelector(container);
                if (!selObj || !selObj.query) continue;

                if (seenSelectors.has(selObj.query)) continue;
                seenSelectors.add(selObj.query);

                let score = text.length;
                let queryStr = selObj.query.toLowerCase();

                // 🚀 가중치 시스템 개편 (댓글 및 게시판 구조 우대)
                if (['article', 'main'].includes(queryStr.split('.')[0])) score += 5000;
                if (queryStr.includes('content') || queryStr.includes('body') || queryStr.includes('post')) score += 3000;
                if (queryStr.includes('title') || queryStr.includes('subject')) score += 1500;
                
                // 테이블(게시판) 구조 가중치 부여
                if (queryStr.includes('td') || queryStr.includes('tr')) score += 2000;
                
                // 🚀 댓글 영역 감지 시 초고도 가중치 부여
                if (queryStr.includes('comment') || queryStr.includes('reply') || queryStr.includes('cmt')) score += 4000;

                // SDK 호환성을 위해 tag에 완성된 쿼리를 넣고 className은 비워둠
                results.push({ tag: selObj.query, className: "", score: score });
            }
            return results.sort((a, b) => b.score - a.score).slice(0, 10); // 추출량 10개로 증가
        }""")

# ==========================================
# 3. Navigator (탐색기)
# ==========================================
class Navigator:
    def __init__(self, start_url, log_queue=None):
        self.start_url = start_url
        self.target_domain = urlparse(start_url).netloc
        self.log_queue = log_queue
        self.visited_urls = set()
        self.queue = [start_url]
        self.final_structures = {}

    def log(self, category, message):
        now = time.strftime("%H:%M:%S")
        msg = f"[{now}] [{category}] {message}"
        print(msg)
        if self.log_queue:
            self.log_queue.put_nowait(msg)

    def get_mid_from_url(self, url):
        path_parts = urlparse(url).path.strip('/').split('/')
        return path_parts[0] if path_parts and path_parts[0] else "main"

    async def collect_links(self, page):
        raw_links = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)")
        
        found_detail_pages = 0
        for link in raw_links:
            if self.target_domain in link and "write" not in link:
                if link not in self.visited_urls and link not in self.queue:
                    self.queue.append(link)
                    if link.split('/')[-1].isdigit():
                        found_detail_pages += 1
                        
        if found_detail_pages > 0:
            self.log("LINK_FIND", f"🎯 상세 페이지 <a> 태그 {found_detail_pages}개 감지 및 스케줄링 완료")

    async def run(self):
        self.log("SYSTEM", f"탐색 모듈 가동 (Target: {self.start_url})")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            
            headers = {"ngrok-skip-browser-warning": "true"}
            context = await browser.new_context(extra_http_headers=headers)
            page = await context.new_page()

            while self.queue and len(self.visited_urls) < Config.MAX_PAGES:
                current_url = self.queue.pop(0)
                if current_url in self.visited_urls: continue
                
                self.visited_urls.add(current_url)
                mid = self.get_mid_from_url(current_url)

                try:
                    self.log("NAVIGATE", f"진입: {current_url}")
                    
                    response = await page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
                    if not response or response.status >= 400:
                        continue

                    await page.wait_for_timeout(Config.HYDRATION_DELAY * 1000)

                    page_text = await page.evaluate("() => document.body.innerText.replace(/\\n/g, ' ').substring(0, 60)")
                    self.log("VISION", f"렌더링 스냅샷: {page_text}...")

                    selectors = await DOMParser.extract_main_content(page)
                    
                    if mid not in self.final_structures:
                        self.final_structures[mid] = {"url_pattern": current_url, "selectors": []}

                    # 🚀 중복 검사 로직 변경: tag에 전체 쿼리가 들어가므로 tag 자체로 비교
                    existing_keys = {s['tag'] for s in self.final_structures[mid]["selectors"]}
                    for sel in selectors:
                        key = sel['tag']
                        if key not in existing_keys:
                            self.final_structures[mid]["selectors"].append(sel)
                            existing_keys.add(key)
                            self.log("PARSER", f"[{mid}] 수집 완료: {key}")

                    await self.collect_links(page)

                except Exception as e:
                    self.log("ERROR", f"스캔 예외 ({current_url}): {str(e)}")

            await browser.close()
            self.log("SYSTEM", "스캔 및 아키텍처 매핑 완료.")
            return self.final_structures

# ==========================================
# 4. API 브릿지 (스트리밍 인터페이스)
# ==========================================
class IntegratedAnalyzer:
    def __init__(self, start_url):
        self.start_url = start_url

    async def analyze(self):
        log_queue = asyncio.Queue()
        navigator = Navigator(self.start_url, log_queue)
        
        task = asyncio.create_task(navigator.run())
        
        while not task.done() or not log_queue.empty():
            try:
                msg = await asyncio.wait_for(log_queue.get(), timeout=0.1)
                yield msg + "\n\n"
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                yield f"[ERROR] 스트림 에러: {str(e)}\n\n"
                break
                
        try:
            final_data = await task 
            result_json = json.dumps(final_data, ensure_ascii=False)
            yield f"[RESULT] {result_json}\n\n"
        except Exception as e:
            yield f"[ERROR] 반환 실패: {str(e)}\n\n"