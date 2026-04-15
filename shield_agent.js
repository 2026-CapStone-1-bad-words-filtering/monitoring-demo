(function () {
  // 1. 브라우저의 진짜 fetch를 따로 보관해둡니다 (나중에 깨끗한 것만 보내야 하니까요)
  const originalFetch = window.fetch;

  // 2. fetch 함수를 우리가 만든 로직으로 덮어씌웁니다 (Monkey Patch)
  window.fetch = async (...args) => {
    let [resource, config] = args;

    // 3. POST 요청이고 본문(body)이 있는 경우만 검사합니다.
    if (config && config.method === "POST" && config.body) {
      console.warn("🛡️ [ShieldAgent] 전송 데이터 포착!", resource);

      const payload = JSON.parse(config.body);
      console.log("🔍 검사 중인 텍스트:", payload.content);

      // 4. [검문소] 비속어가 있는지 확인 (여기서는 간단히 '바보'만 체크)
      if (payload.content.includes("바보")) {
        alert("❌ [ShieldAgent] 비속어가 감지되어 전송이 차단되었습니다!");

        // 진짜 fetch를 실행하지 않고 에러 응답을 반환하여 중단시킵니다.
        return new Response(
          JSON.stringify({ status: "blocked", reason: "swear word" }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        );
      }
    }

    // 5. 검문을 통과했다면 원래 가려던 길(진짜 fetch)로 보내줍니다.
    return originalFetch(...args);
  };

  console.log("✅ [ShieldAgent] 네트워크 후킹 가동 중...");
})();
