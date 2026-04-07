import asyncio
import json
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key="YOUR_OPENAI_API_KEY")

async def verify_selector(page, selector):
    """실제 브라우저에서 셀렉터가 유효한지 검증하는 함수"""
    try:
        # 셀렉터가 존재하고 화면에 보이는지 확인
        count = await page.locator(selector).count()
        if count == 0:
            return "에러: 해당 셀렉터로 요소를 찾을 수 없습니다."
        if count > 1:
            return f"에러: 해당 셀렉터로 {count}개의 요소가 발견되었습니다. 더 구체적인 셀렉터가 필요합니다."
        
        is_visible = await page.locator(selector).is_visible()
        if not is_visible:
            return "에러: 요소는 존재하지만 화면에 보이지 않습니다(Hidden)."
        
        return "SUCCESS"
    except Exception as e:
        return f"에러: 유효하지 않은 셀렉터 문법입니다. ({str(e)})"

async def analyze_with_self_correction(url):
    print(f"🕵️ [ShieldAgent] {url} 분석 및 자동 검증 시작...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        
        # 1. 가성비 DOM 데이터 추출
        compact_dom = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('input, textarea, button, form')).map(el => ({
                t: el.tagName, i: el.id, c: el.className, n: el.name, p: el.placeholder || '', v: (el.innerText || el.value || '').substring(0,15)
            }));
        }""")

        max_retries = 3
        feedback = "첫 번째 시도입니다."
        final_result = None

        for i in range(max_retries):
            print(f"🔄 시도 {i+1}/3...")
            
            # 2. LLM에게 분석 요청 (이전 피드백 포함)
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "너는 웹 자동화 전문가야. 반드시 단 하나의 요소를 가리키는 고유한 CSS Selector를 JSON으로 반환해."},
                    {"role": "user", "content": f"URL: {url}\nDOM: {json.dumps(compact_dom)}\n피드백: {feedback}"}
                ],
                response_format={ "type": "json_object" }
            )
            
            prediction = json.loads(response.choices[0].message.content)
            in_sel = prediction.get('input_selector')
            btn_sel = prediction.get('button_selector')

            # 3. 실시간 검증 (실제 브라우저에서 실행)
            in_status = await verify_selector(page, in_sel)
            btn_status = await verify_selector(page, btn_sel)

            if in_status == "SUCCESS" and btn_status == "SUCCESS":
                print("✅ 검증 완료! 완벽한 셀렉터를 찾았습니다.")
                final_result = prediction
                break
            else:
                # 4. 실패 시 피드백 생성 후 다시 루프
                feedback = f"입력창 결과: {in_status} / 버튼 결과: {btn_status}"
                print(f"⚠️ 검증 실패: {feedback}")

        await browser.close()
        return final_result

# 실행
if __name__ == "__main__":
    url = "https://your-target-site.com/write" # 실제 카페24 등 주소 입력
    result = asyncio.run(analyze_with_self_correction(url))
    print("\n최종 결과:", json.dumps(result, indent=2))