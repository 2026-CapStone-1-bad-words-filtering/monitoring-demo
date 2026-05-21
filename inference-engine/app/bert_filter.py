import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline
from pathlib import Path
from .schemas import StageResult

class BertProfanityFilter:
    def __init__(self, model_dir: str, threshold: float = 0.8):
        self.model_dir = Path(model_dir)
        self.threshold = threshold
        self.labels = {"LABEL_0": "정상", "LABEL_1": "부적절", "0": "정상", "1": "부적절"}
        
        print(f"[AI] 모델 로딩 시작: {self.model_dir}")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.model_dir)
        self.device = 0 if torch.cuda.is_available() else -1
        self.classifier = pipeline(
            "text-classification", 
            model=self.model, 
            tokenizer=self.tokenizer, 
            device=self.device
        )
        print("[AI] 모델 로딩 완료!")

    def evaluate(self, text: str) -> StageResult:
        result = self.classifier(text)[0]
        raw_label = str(result["label"])
        label = self.labels.get(raw_label, raw_label)
        score = float(result["score"])
        
        is_inappropriate = (label == "부적절") and (score >= self.threshold)

        return StageResult(
            is_inappropriate=is_inappropriate,
            label=label,
            score=score,
            reason="2단계(BERT): 악의적 문맥 또는 부적절 표현 감지됨." if is_inappropriate else "2단계(BERT): 정상",
            details={"rawLabel": raw_label, "threshold": self.threshold}
        )