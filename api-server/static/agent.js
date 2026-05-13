async function scanAndFilterDOM(selectors) {
  selectors.forEach((sel) => {
    const query = buildQuery(sel);
    const elements = document.querySelectorAll(query);

    elements.forEach((el) => {
      if (el.dataset.isFiltered === "true") return;

      // 텍스트가 있는지 확인
      const rawText = el.innerText || el.textContent;
      if (!rawText) return;

      // [강력 테스트] 단어 포함 여부 확인
      const targetWords = ["시발", "미친"];
      let detected = false;

      targetWords.forEach((word) => {
        if (rawText.includes(word)) {
          console.log(
            `[🎯 TARGET DETECTED] 발견된 단어: ${word} | 위치: ${query}`,
          );
          detected = true;
        }
      });

      if (detected) {
        // 일단 통째로 별표로 바꿔서 작동하는지 확인!
        el.innerHTML = "🚫 필터링된 콘텐츠입니다.";
        el.style.color = "red";
        el.style.fontWeight = "bold";
      }

      el.dataset.isFiltered = "true";
    });
  });
}
