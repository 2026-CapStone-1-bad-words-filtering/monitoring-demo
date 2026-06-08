import time
import torch
from .trie_filter import TrieStage
from .bert_filter import BertProfanityFilter

import time
import torch
from .trie_filter import TrieStage
from .bert_filter import BertProfanityFilter

class ModerationPipeline:
    def __init__(self, model_dir: str):
        if torch.get_num_threads() > 4:
            torch.set_num_threads(4)
        self.trie_stage = TrieStage()
        self.bert_stage = BertProfanityFilter(model_dir=model_dir, threshold=0.55) # 변형 비속어 감지용 민감도 튜닝 유지

    def run(self, content: str) -> dict:
        content = content.strip()
        if not content:
            return {"isInappropriate": False, "stage": "safe", "reason": "입력 텍스트가 없습니다."}

        print(f"\n[PIPELINE_FLOW] 🚀 정밀 검사 가동 (원본 문맥 수신)")

        # [1단계] 명시적 비속어 및 오타(Trie) 검사
        trie_result = self.trie_stage.evaluate(content)
        if trie_result.is_inappropriate:
            return {"isInappropriate": True, "stage": "trie", "reason": trie_result.reason}

        # [2단계] 문맥 기반 비속어(BERT) 검사
        with torch.no_grad():
            bert_result = self.bert_stage.evaluate(content)
            
        if bert_result.is_inappropriate:
            return {"isInappropriate": True, "stage": "bert", "reason": bert_result.reason}

        return {"isInappropriate": False, "stage": "safe", "reason": "안전한 콘텐츠입니다."}
    
    # 🚀 [추가할 부분] 텐서 배치 처리를 위한 대량 검사 전용 함수
    def run_batch(self, contents: list[str]) -> list[dict]:
        # 결과를 담을 껍데기를 미리 만들어 둡니다. (기본값: 안전)
        results = [{"isInappropriate": False, "stage": "safe", "reason": "안전한 콘텐츠입니다."} for _ in range(len(contents))]
        
        bert_pending_indices = []
        bert_pending_texts = []

        # 🛡️ [1단계] Trie 고속 검사 (CPU에서 엄청 빠르므로 for문으로 1차로 쳐냅니다)
        for i, text in enumerate(contents):
            clean_text = text.strip()
            if not clean_text:
                results[i] = {"isInappropriate": False, "stage": "safe", "reason": "입력 텍스트가 없습니다."}
                continue
                
            trie_result = self.trie_stage.evaluate(clean_text)
            
            if trie_result.is_inappropriate:
                # 명백한 욕설은 여기서 바로 차단 판정! (BERT로 안 감)
                results[i] = {"isInappropriate": True, "stage": "trie", "reason": trie_result.reason}
            else:
                # Trie를 통과한 애매한 문장들만 BERT 대기열에 탑승시킵니다.
                bert_pending_indices.append(i)
                bert_pending_texts.append(clean_text)

        # 🧠 [2단계] 문맥 기반 비속어(BERT) 대량 행렬 검사
        if bert_pending_texts:
            print(f"[PIPELINE_FLOW] 🚀 BERT 배치 연산 가동 (총 {len(bert_pending_texts)}개 문장 동시 처리)")
            
            with torch.no_grad():
                # 🔥 [핵심] 배열을 통째로 넘겨서 한 번의 GPU/CPU 연산으로 끝냅니다!
                bert_results = self.bert_stage.evaluate_batch(bert_pending_texts)
                
                # 나온 결과를 원래 배열의 위치(index)에 맞게 꽂아줍니다.
                for idx, b_result in zip(bert_pending_indices, bert_results):
                    if b_result.is_inappropriate:
                        results[idx] = {"isInappropriate": True, "stage": "bert", "reason": b_result.reason}

        return results