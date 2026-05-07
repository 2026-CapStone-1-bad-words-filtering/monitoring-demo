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
    def __init__(self, start_url, db_session=None, max_pages=30, delay_range=(1.5, 2.5)):
        self.start_url = start_url
        self.domain = urlparse(start_url).netloc
        self.max_pages = max_pages
        self.db_session = db_session
        self.auth_file = os.path.join("auth", "login_state.json")
        self.delay_range = delay_range
        
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

        if 'mid' in qs:
            return qs['mid'][0]

        path_parts = parsed.path.strip('/').split('/')

        return path_parts[0] if path_parts and path_parts[0] else "main"

    def debug(self, category, message):
        now = time.strftime("%H:%M:%S")
        print(f"[{now}] [{category}] {message}")

    async def run(self):
        async with async_playwright() as p:

            print(f"\n{'='*80}")
            print(f"🚀 [SYSTEM] 통합 분석 엔진 가동")
            print(f"🌐 DOMAIN : {self.domain}")
            print(f"📄 START  : {self.start_url}")
            print(f"📦 MAX_PAGE : {self.max_pages}")
            print(f"{'='*80}")

            browser = await p.chromium.launch(
                headless=False,
                slow_mo=150
            )

            self.debug("BROWSER", "Chromium 실행 완료")

            context = await browser.new_context(
                storage_state=self.auth_file if os.path.exists(self.auth_file) else None
            )

            self.debug(
                "AUTH",
                f"로그인 세션 {'로드 성공' if os.path.exists(self.auth_file) else '없음'}"
            )

            page = await context.new_page()

            # =========================
            # 콘솔 로그 추적
            # =========================
            page.on(
                "console",
                lambda msg: print(
                    f"[BROWSER_CONSOLE] {msg.type.upper()} :: {msg.text}"
                )
            )

            # =========================
            # JS 에러 추적
            # =========================
            page.on(
                "pageerror",
                lambda err: print(f"[PAGE_ERROR] {err}")
            )

            # =========================
            # Request 추적
            # =========================
            page.on(
                "request",
                lambda req: print(
                    f"[REQ] {req.method:<6} {req.resource_type:<10} {req.url[:120]}"
                )
            )

            # =========================
            # Response 추적
            # =========================
            page.on(
                "response",
                lambda res: print(
                    f"[RES] {res.status:<4} {res.request.resource_type:<10} {res.url[:120]}"
                )
            )

            # --- [DEBUG] 네트워크 인터셉트: POST 패킷 가로채기 ---
            async def intercept_route(route):

                request = route.request

                if request.method == "POST":

                    curr_mid = self.extract_mid(page.url)

                    data = request.post_data or ""

                    print("\n" + "="*80)
                    self.debug("POST_CAPTURE", f"mid={curr_mid}")
                    self.debug("ENDPOINT", request.url)
                    self.debug("METHOD", request.method)

                    headers = request.headers

                    self.debug(
                        "HEADERS",
                        json.dumps(headers, indent=2, ensure_ascii=False)[:1200]
                    )

                    if data:
                        self.debug("PAYLOAD_LEN", str(len(data)))
                        self.debug("PAYLOAD_RAW", data[:3000])

                        try:
                            parsed_payload = parse_qs(data)

                            self.debug(
                                "PAYLOAD_PARSED",
                                json.dumps(parsed_payload, indent=2, ensure_ascii=False)[:3000]
                            )

                        except Exception as e:
                            self.debug("PAYLOAD_PARSE_FAIL", str(e))

                    else:
                        self.debug("PAYLOAD", "EMPTY")

                    print("="*80 + "\n")

                    if curr_mid not in self.final_structures:
                        self.final_structures[curr_mid] = {
                            "get_selectors": {},
                            "post_metadata": {}
                        }

                    self.final_structures[curr_mid]["post_metadata"] = {
                        "endpoint": request.url,
                        "payload_sample": data[:2000],
                        "fields": list(parse_qs(data).keys()) if data else [],
                        "headers": headers
                    }

                    # 실제 전송 막기
                    await route.abort()

                    self.debug(
                        "POST_BLOCK",
                        "실제 POST 전송 차단 완료"
                    )

                else:
                    await route.continue_()

            await page.route("**/*", intercept_route)

            while self.queue and len(self.visited_urls) < self.max_pages:

                self.debug(
                    "QUEUE",
                    f"남은 URL 수: {len(self.queue)}"
                )

                current_url = self.queue.pop(0)

                if current_url in self.visited_urls:
                    self.debug("SKIP", f"이미 방문함: {current_url}")
                    continue

                self.visited_urls.add(current_url)

                mid = self.extract_mid(current_url)

                print("\n" + "-"*80)
                self.debug("CRAWL_START", current_url)
                self.debug("MID", mid)
                self.debug(
                    "VISITED",
                    f"{len(self.visited_urls)} / {self.max_pages}"
                )
                print("-"*80)

                try:

                    delay = random.uniform(*self.delay_range)

                    self.debug(
                        "WAIT",
                        f"{delay:.2f}초 랜덤 대기"
                    )

                    await asyncio.sleep(delay)

                    t_start = time.time()

                    self.debug("GOTO", "페이지 이동 시작")

                    response = await page.goto(current_url, wait_until="networkidle", timeout=15000)

                    load_time = time.time() - t_start

                    self.debug(
                        "GOTO_DONE",
                        f"status={response.status if response else 'N/A'} "
                        f"time={load_time:.2f}s"
                    )

                    self.debug(
                        "PAGE_INFO",
                        f"title={await page.title()}"
                    )

                    # 현재 URL 변경 체크
                    self.debug(
                        "CURRENT_URL",
                        page.url
                    )

                    # =========================
                    # GET 구조 분석
                    # =========================
                    if mid not in self.analyzed_mids:

                        print("\n" + "="*40)
                        self.debug(
                            "GET_ANALYZE",
                            f"'{mid}' 구조 분석 시작"
                        )
                        print("="*40)

                        get_rules = await self.analyze_get_structure(page)

                        self.debug(
                            "GET_RESULT_COUNT",
                            str(len(get_rules))
                        )

                        for idx, item in enumerate(get_rules[:10], 1):
                            self.debug(
                                f"TOP_{idx}",
                                f"[score={item.get('score')}] "
                                f"class={item.get('className')} "
                                f"text={item.get('text')[:120]}"
                            )

                        if mid not in self.final_structures:
                            self.final_structures[mid] = {
                                "get_selectors": {},
                                "post_metadata": {}
                            }

                        self.final_structures[mid]["get_selectors"] = get_rules
                        self.final_structures[mid]["url_pattern"] = current_url

                        # =========================
                        # POST 패킷 유도
                        # =========================
                        if mid != "main":

                            self.debug(
                                "POST_TRY",
                                f"'{mid}' 글쓰기 진입 시도"
                            )

                            await self.try_post_capture(page, mid)

                        self.analyzed_mids.add(mid)

                        self.debug(
                            "ANALYZE_DONE",
                            f"{mid} 완료 ({time.time() - t_start:.2f}s)"
                        )

                    # =========================
                    # 링크 수집
                    # =========================
                    before_queue = len(self.queue)

                    await self.collect_links(page)

                    after_queue = len(self.queue)

                    self.debug(
                        "LINK_COLLECT",
                        f"+{after_queue - before_queue}개 URL 추가"
                    )

                except Exception as e:

                    self.debug(
                        "ERROR",
                        f"{type(e).__name__}: {str(e)}"
                    )

            await browser.close()

            self.debug("BROWSER", "브라우저 종료 완료")

            self.print_summary()

            return self.final_structures

    async def analyze_get_structure(self, page):

        self.debug(
            "DOM_ANALYZE",
            "TreeWalker 실행 시작"
        )

        result = await page.evaluate("""() => {

            const ignoreSelectors =
                'nav, header, footer, aside, script, style, noscript, svg, .gnb, .sidebar, .menu, .pagination';

            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                null,
                false
            );

            let node;

            const seenTexts = new Set();

            const results = [];

            const decodeHTMLEntities = (text) => {
                const textArea = document.createElement('textarea');
                textArea.innerHTML = text;
                return textArea.value;
            };

            while (node = walker.nextNode()) {

                let text = node.nodeValue.trim();

                if (
                    text.length < 5 ||
                    /^[0-9\\-. :]+$/.test(text)
                ) continue;

                let container = node.parentElement;

                if (
                    !container ||
                    container.closest(ignoreSelectors) ||
                    container.offsetParent === null
                ) continue;

                while (
                    container &&
                    container !== document.body &&
                    !container.className &&
                    !container.id
                ) {
                    container = container.parentElement;
                }

                let cls = (container.className || "")
                    .toString()
                    .trim();

                let fullText = decodeHTMLEntities(
                    container.innerText || text
                )
                .replace(/\\s+/g, ' ')
                .trim();

                if (seenTexts.has(fullText)) continue;

                seenTexts.add(fullText);

                let score = fullText.length;

                const lowerCls = cls.toLowerCase();

                if (
                    lowerCls.includes('title') ||
                    lowerCls.includes('subject')
                ) score += 3000;

                if (
                    lowerCls.includes('content') ||
                    lowerCls.includes('article') ||
                    lowerCls.includes('xe_content')
                ) score += 5000;

                results.push({
                    tag: container.tagName.toLowerCase(),
                    className: cls,
                    text: fullText.substring(0, 500),
                    score: score
                });
            }

            return results
                .sort((a, b) => b.score - a.score)
                .slice(0, 40);
        }""")

        self.debug(
            "DOM_ANALYZE_DONE",
            f"{len(result)}개 노드 분석 완료"
        )

        return result

    async def try_post_capture(self, page, mid):
        """글쓰기 버튼을 눌러 실제 전송 패킷을 발생시킴 (디버그 로그 포함)"""

        try:

            self.debug(
                "POST_CAPTURE",
                f"{mid} 게시판 글쓰기 탐색 시작"
            )

            write_regex = "|".join(self.write_keywords)

            self.debug(
                "WRITE_REGEX",
                write_regex
            )

            write_btn = page.locator(
                'a, button'
            ).filter(
                has_text=re.compile(f"^({write_regex})$", re.I)
            ).first

            count = await write_btn.count()

            self.debug(
                "WRITE_BTN_COUNT",
                str(count)
            )

            if await write_btn.is_visible(timeout=2000):

                self.debug(
                    "WRITE_BTN",
                    "글쓰기 버튼 발견"
                )

                btn_text = await write_btn.inner_text()

                self.debug(
                    "WRITE_BTN_TEXT",
                    btn_text
                )

                await write_btn.click()

                self.debug(
                    "WRITE_CLICK",
                    "글쓰기 버튼 클릭 완료"
                )

                await page.wait_for_load_state("domcontentloaded")

                self.debug(
                    "WRITE_PAGE",
                    f"현재 URL: {page.url}"
                )

                # 폼 입력
                self.debug(
                    "FORM_FILL",
                    "입력 필드 자동 채우기 시작"
                )

                await page.evaluate("""() => {

                    document.querySelectorAll(
                        'input[type="text"], textarea, [contenteditable="true"]'
                    ).forEach(f => {

                        f.value = "DEBUG_CONTENT";

                        f.innerHTML = "DEBUG_CONTENT";
                    });
                }""")

                self.debug(
                    "FORM_FILL_DONE",
                    "입력 완료"
                )

                submit_regex = "|".join(self.submit_keywords)

                self.debug(
                    "SUBMIT_REGEX",
                    submit_regex
                )

                submit_btn = page.locator(
                    'button, input[type="submit"]'
                ).filter(
                    has_text=re.compile(f"^({submit_regex})$", re.I)
                ).first

                if await submit_btn.is_visible(timeout=2000):

                    self.debug(
                        "SUBMIT_BTN",
                        "등록 버튼 발견"
                    )

                    try:
                        submit_text = await submit_btn.inner_text()

                        self.debug(
                            "SUBMIT_TEXT",
                            submit_text
                        )

                    except:
                        pass

                    self.debug(
                        "SUBMIT_CLICK",
                        "등록 버튼 클릭 시도"
                    )

                    await submit_btn.click(no_wait_after=True)

                    self.debug(
                        "SUBMIT_CLICK_DONE",
                        "클릭 완료, POST 대기"
                    )

                    await asyncio.sleep(1.5)

                else:

                    self.debug(
                        "SUBMIT_NOT_FOUND",
                        "등록 버튼 탐색 실패"
                    )

            else:

                self.debug(
                    "WRITE_NOT_FOUND",
                    "글쓰기 버튼 탐색 실패"
                )

            return True

        except Exception as e:

            self.debug(
                "POST_CAPTURE_FAIL",
                f"{type(e).__name__}: {str(e)}"
            )

            return False

    async def collect_links(self, page):

        self.debug(
            "LINK_SCAN",
            "페이지 링크 수집 시작"
        )

        links = await page.evaluate("""
            () => Array.from(
                document.querySelectorAll('a[href]')
            ).map(el => el.href)
        """)

        self.debug(
            "LINK_TOTAL",
            f"{len(links)}개 href 발견"
        )

        added = 0

        for link in links:

            if (
                link.startswith(self.start_url) and
                link not in self.visited_urls
            ):

                self.queue.append(link)
                added += 1

        self.debug(
            "LINK_ADDED",
            f"{added}개 queue 추가"
        )

    def print_summary(self):

        print("\n" + "="*80)
        print("📊 [SUMMARY] 사이트 구조 분석 리포트")
        print("="*80)

        print(f"🌐 DOMAIN      : {self.domain}")
        print(f"📄 VISITED URL : {len(self.visited_urls)}")
        print(f"📦 ANALYZED MID: {len(self.analyzed_mids)}")

        print("-"*80)

        for mid, data in self.final_structures.items():

            print(f"\n📌 게시판 ID : {mid}")

            print(f"   URL PATTERN")
            print(f"   └─ {data.get('url_pattern', 'N/A')}")

            get_data = data.get("get_selectors", [])

            print(f"\n   GET SELECTORS ({len(get_data)}개)")
            for idx, item in enumerate(get_data[:5], 1):

                print(
                    f"   [{idx}] "
                    f"score={item.get('score')} "
                    f"class={item.get('className')} "
                    f"text={item.get('text')[:80]}"
                )

            post = data.get("post_metadata", {})

            print(f"\n   POST")
            print(f"   ├─ endpoint : {post.get('endpoint', 'N/A')}")
            print(f"   ├─ fields   : {post.get('fields', [])}")

            payload = post.get("payload_sample", "")

            if payload:
                print(f"   └─ payload  : {payload[:300]}")

        print("\n" + "="*80)
        print("✅ 분석 종료")
        print("="*80 + "\n")