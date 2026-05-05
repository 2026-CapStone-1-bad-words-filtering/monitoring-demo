import os
import json
import asyncio
import re
import time
from urllib.parse import urlparse, parse_qs, urlunparse
from playwright.async_api import async_playwright

class SmartPacketSpider:
    def __init__(self, start_url, max_pages=50):
        self.start_url = start_url
        self.max_pages = max_pages
        self.auth_file = os.path.join("auth", "login_state.json")
        
        # 1. 텍스트 데이터셋 세분화
        self.write_keywords = {"글쓰기", "쓰기", "작성", "Write", "New Post", "Create"}
        self.submit_keywords = {"등록", "확인", "완료", "저장", "게시", "전송", "Submit", "Publish", "Done"}
        
        self.visited_urls = set()
        self.analyzed_mids = set() 
        self.captured_packets = []
        self.queue = [start_url]

    def normalize_url(self, url):
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

    def extract_mid(self, url):
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if 'mid' in qs: return qs['mid'][0]
        path_parts = parsed.path.strip('/').split('/')
        return path_parts[0] if path_parts and path_parts[0] else "main"

    async def run(self):
        if not os.path.exists(self.auth_file):
            print("❌ 세션 파일 없음!")
            return

        async with async_playwright() as p:
            print(f"🚀 스캔 가동: {self.start_url}")
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(storage_state=self.auth_file)
            page = await context.new_page()

            # 패킷 인터셉트 설정
            async def intercept_route(route):
                if route.request.method == "POST":
                    print(f"🎯 [PACKET] POST 낚시 성공: {route.request.url[:50]}")
                    self.captured_packets.append({
                        "url": route.request.url,
                        "data": route.request.post_data,
                        "page": page.url
                    })
                    await route.abort()
                else:
                    await route.continue_()
            
            await page.route("**/*", intercept_route)

            while self.queue and len(self.visited_urls) < self.max_pages:
                current_url = self.queue.pop(0)
                norm_url = self.normalize_url(current_url)
                
                if norm_url in self.visited_urls: continue
                self.visited_urls.add(norm_url)

                mid = self.extract_mid(current_url)
                print(f"\n🌐 방문: {current_url} (mid: {mid})")

                try:
                    await page.goto(current_url, wait_until="domcontentloaded", timeout=15000)
                    
                    # [단계 1] 새로운 게시판 유형이면 '글쓰기' 페이지를 먼저 찾음
                    if mid not in self.analyzed_mids and mid != "main":
                        print(f"  🔍 [{mid}] 게시판 글쓰기 관문 찾는 중...")
                        if await self.go_to_write_and_submit(page, mid):
                            self.analyzed_mids.add(mid)

                    # [단계 2] 링크 수집
                    await self.collect_links(page)

                except Exception as e:
                    print(f"  ⚠️ 오류 스킵: {str(e)[:30]}")

            await browser.close()
            with open("captured_packets.json", "w", encoding="utf-8") as f:
                json.dump(self.captured_packets, f, ensure_ascii=False, indent=2)
            print(f"\n🏁 종료. 저장된 패킷: {len(self.captured_packets)}건")

    async def go_to_write_and_submit(self, page, mid):
        """목록에서 글쓰기 버튼을 찾아 이동한 뒤 등록 패킷을 땁니다."""
        try:
            # 1. '글쓰기' 버튼 탐색 (목록 페이지에서)
            write_regex = "|".join(self.write_keywords)
            write_btn = page.locator('a, button').filter(has_text=re.compile(f"^({write_regex})$", re.I)).first
            
            if await write_btn.is_visible(timeout=2000):
                print(f"  📝 '글쓰기' 버튼 발견, 이동합니다.")
                await write_btn.click()
                await page.wait_for_load_state("domcontentloaded")
                
                # 2. 이동한 페이지에 입력창이 있는지 확인
                if await page.locator('textarea, [contenteditable="true"]').first.is_visible(timeout=3000):
                    print("  🖋️ 작성 페이지 진입 성공. 데이터 주입...")
                    return await self.perform_submit(page)
            else:
                # 글쓰기 버튼이 없으면 이미 작성 페이지인지 확인 (직접 링크 타고 온 경우)
                if await page.locator('textarea, [contenteditable="true"]').first.is_visible(timeout=500):
                    return await self.perform_submit(page)
            
            return False
        except:
            return False

    async def perform_submit(self, page):
        """실제 본문 입력 후 등록 버튼을 눌러 패킷을 생성합니다."""
        try:
            # 데이터 주입
            await page.evaluate("""() => {
                const fields = document.querySelectorAll('input[type="text"], textarea, [contenteditable="true"]');
                fields.forEach(f => {
                    if(f.tagName === 'INPUT' || f.tagName === 'TEXTAREA') f.value = 'PACKET_COLLECT_DATA';
                    else f.innerHTML = 'PACKET_COLLECT_DATA';
                });
            }""")

            # '등록' 버튼 탐색 (본문 입력창 주변의 버튼만 타겟팅)
            submit_regex = "|".join(self.submit_keywords)
            submit_btn = page.locator('button, input[type="submit"], .btn_confirm').filter(
                has_text=re.compile(f"^({submit_regex})$", re.I)
            ).first

            if await submit_btn.is_visible(timeout=2000):
                print(f"  🔘 전송 버튼 클릭! ({await submit_btn.inner_text()})")
                await submit_btn.click(no_wait_after=True)
                await asyncio.sleep(1.5) # 패킷 낚시 대기 시간 확보
                return True
            
            print("  ⏭️ 본문은 찾았으나 전송 버튼을 못 찾음")
            return False
        except:
            return False

    async def collect_links(self, page):
        links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]'))
                .map(el => el.href)
                .filter(href => href.startsWith(window.location.origin) && !href.includes('facebook') && !href.includes('twitter'));
        }""")
        for link in links:
            if self.normalize_url(link) not in self.visited_urls:
                self.queue.append(link)

if __name__ == "__main__":
    spider = SmartPacketSpider("https://zeropage.org", max_pages=50)
    asyncio.run(spider.run())