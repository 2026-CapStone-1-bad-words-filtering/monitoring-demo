from .trie_filter import TrieStage
from .bert_filter import BertProfanityFilter

class ModerationPipeline:
    def __init__(self, model_dir: str):
        self.trie_stage = TrieStage()
        self.bert_stage = BertProfanityFilter(model_dir=model_dir, threshold=0.8)

    def run(self, content: str) -> dict:
        content = content.strip()
        if not content:
            return {"isInappropriate": False, "stage": "safe", "reason": "입력 텍스트가 없습니다."}

        # [1단계] 명시적 비속어 및 오타(Trie) 검사
        trie_result = self.trie_stage.evaluate(content)
        if trie_result.is_inappropriate:
            return {"isInappropriate": True, "stage": "trie", "reason": trie_result.reason}

        # [2단계] 문맥 기반 비속어(BERT) 검사
        bert_result = self.bert_stage.evaluate(content)
        if bert_result.is_inappropriate:
            return {"isInappropriate": True, "stage": "bert", "reason": bert_result.reason}

        # 모두 통과!
        return {"isInappropriate": False, "stage": "safe", "reason": "안전한 콘텐츠입니다."}