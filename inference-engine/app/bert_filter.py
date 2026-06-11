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
        
        # 🚀 [수정] 파이프라인용(hf)과 파이토치용(torch) 디바이스를 분리합니다.
        self.hf_device = 0 if torch.cuda.is_available() else -1
        self.torch_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # 직접 텐서 연산을 하기 위해 모델 자체를 장치(GPU/CPU)에 명시적으로 올립니다.
        self.model.to(self.torch_device)

        self.classifier = pipeline(
            "text-classification", 
            model=self.model, 
            tokenizer=self.tokenizer, 
            device=self.hf_device
        )
        print(f"[AI] 모델 로딩 완료! (장치: {self.torch_device})")

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
    
    def evaluate_batch(self, texts: list[str]) -> list[StageResult]:
        """
        ⚡ 텐서 배치 연산 (Tensor Batching) + 초간단 속도 최적화
        """
        # 1. 🚀 속도 최적화 1: max_length=128 제거 (Dynamic Padding)
        # 들어온 문장 중 '가장 긴 문장' 길이에 맞춰 행렬 크기가 자동으로 줄어듭니다.
        inputs = self.tokenizer(
            texts, 
            return_tensors="pt", 
            padding=True, 
            truncation=True
        )
        
        inputs = {k: v.to(self.torch_device) for k, v in inputs.items()}
        
        # 2. 🚀 속도 최적화 2: 파이토치 초고속 추론 모드 적용
        with torch.inference_mode():
            outputs = self.model(**inputs)
        
        logits = outputs.logits
        probs = torch.softmax(logits, dim=-1)
        
        # 3. 🎯 과탐지(False Positive) 해결: 무조건 인덱스 1을 뽑지 않고,
        # 모델이 가장 확신하는 1등 확률(max_probs)과 그 라벨(predicted_indices)을 찾습니다.
        max_probs, predicted_indices = torch.max(probs, dim=-1)
        
        # CPU 메모리로 가져오기
        max_probs = max_probs.cpu().numpy()
        predicted_indices = predicted_indices.cpu().numpy()
        
        batch_results = []
        for prob, idx in zip(max_probs, predicted_indices):
            # 모델의 설정에서 실제 라벨(LABEL_0, LABEL_1 등)을 꺼내서 한글로 변환
            raw_label = self.model.config.id2label[idx]
            label = self.labels.get(raw_label, raw_label)
            
            # 1등으로 예측한 라벨이 '부적절'이고, 그 확률이 임계값 이상일 때만 차단!
            is_bad = bool((label == "부적절") and (prob >= self.threshold))
            score_val = float(prob)
            
            res = StageResult(
                is_inappropriate=is_bad,
                label=label,
                score=score_val,
                reason="2단계(BERT 배치): 악의적 문맥 감지됨." if is_bad else "2단계(BERT 배치): 정상",
                details={"threshold": self.threshold, "batch_mode": True, "rawLabel": raw_label}
            )
            batch_results.append(res)
            
        return batch_results