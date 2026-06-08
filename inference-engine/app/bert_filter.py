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
        ⚡ 텐서 배치 연산 (Tensor Batching)
        여러 문장을 한 번에 토큰화하여 단 1회의 모델 연산으로 끝냅니다.
        """
        # 1. 배열 통째로 토크나이저에 넣기 (직사각형 행렬 생성)
        inputs = self.tokenizer(
            texts, 
            return_tensors="pt", 
            padding=True, 
            truncation=True, 
            max_length=128
        )
        
        # 2. 🚀 [수정] 파이토치 전용 디바이스 객체를 사용하여 GPU/CPU로 이동
        inputs = {k: v.to(self.torch_device) for k, v in inputs.items()}
        
        # 3. 단 한 번의 전진 패스(Forward Pass)
        outputs = self.model(**inputs)
        
        # 4. 결과 해석 및 확률값 추출
        logits = outputs.logits
        probs = torch.softmax(logits, dim=-1)
        
        # 비속어 클래스의 확률 (인덱스 1이 '부적절'이라고 가정)
        # 메모리 누수 방지를 위해 detach() 추가 및 float 캐스팅
        bad_probs = probs[:, 1].detach().cpu().numpy() 
        
        batch_results = []
        for prob in bad_probs:
            # numpy float를 파이썬 기본 bool, float 타입으로 변환 (Pydantic 스키마 호환)
            is_bad = bool(prob >= self.threshold)
            score_val = float(prob)
            label_str = "부적절" if is_bad else "정상"
            
            # 🚀 [수정] 임시 클래스 대신 정식 StageResult 규격 사용
            res = StageResult(
                is_inappropriate=is_bad,
                label=label_str,
                score=score_val,
                reason="2단계(BERT 배치): 악의적 문맥 감지됨." if is_bad else "2단계(BERT 배치): 정상",
                details={"threshold": self.threshold, "batch_mode": True}
            )
            batch_results.append(res)
            
        return batch_results