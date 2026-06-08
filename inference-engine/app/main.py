import os
import time
from fastapi import FastAPI
from typing import List
from pydantic import BaseModel
from .schemas import DetectRequest
from .pipeline import ModerationPipeline

app = FastAPI(title="AI Inference Engine")

# 도커 내부에서 마운트될 모델 폴더 경로 (기본값 설정)
MODEL_DIR = os.getenv("MODEL_DIR", "/app/models/bert_profanity_model")

# 1. 배열 형태를 받기 위한 새로운 스키마 정의
class BatchDetectRequest(BaseModel):
    texts: List[str]

# 서버 켜질 때 딱 한 번만 파이프라인(모델 포함) 적재
print(f"[AI_INIT] ⚙️ {MODEL_DIR} 로부터 딥러닝 검열 가드 모델 로딩 중...")
pipeline = ModerationPipeline(model_dir=MODEL_DIR)
print("[AI_INIT] ✅ BERT 모델 파이프라인 적재 완료. 즉시 추론 가능.")

@app.post("/detect")
def detect_text(request: DetectRequest):
    """
    ⚡ [속도 최적화 및 디버그 버전]
    async def -> def 로 전환하여 무거운 CPU 바운드 추론 연산이 
    FastAPI 이벤트 루프를 블로킹하지 않도록 대형 스레드 풀로 완전 위임합니다.
    """
    start_time = time.time()
    
    # 🔍 [DEBUG] 인입되는 텍스트 원본 및 규격 정밀 출력
    raw_content = request.content
    text_len = len(raw_content) if raw_content else 0
    text_snippet = raw_content[:70] + "..." if text_len > 70 else raw_content
    
    print(f"\n[AI_INFERENCE] 📥 실시간 추론 요청 접수 | 데이터 크기: {text_len}자")
    print(f"[AI_INFERENCE] 📝 입력 페이로드 원본 스니펫: '{text_snippet}'")
    
    # 🔥 실제 AI 딥러닝 추론 연산 실행
    try:
        result = pipeline.run(raw_content)
    except Exception as e:
        print(f"[AI_INFERENCE] ❌ 모델 내부 연산 중 치명적 런타임 에러 발생: {str(e)}")
        return {"isInappropriate": False, "stage": "bert_fallback", "reason": f"인프라 연산 실패: {str(e)}"}
    
    # ⏱️ [PROFILE] 소요 시간 연산 (ms 단위 변환)
    latency_ms = (time.time() - start_time) * 1000
    
    print(f"[AI_INFERENCE] ⏱️ 추론 완료 소요 시간: {latency_ms:.2f}ms")
    print(f"[AI_INFERENCE] 📤 최종 관제탑 반환 데이터: {result}")
    
    return result

@app.post("/detect/batch")
def detect_text_batch(request: BatchDetectRequest):
    """
    ⚡ [Tensor Batching 최적화 라우터]
    여러 개의 문장을 한 번에 받아 GPU/CPU의 행렬 연산을 극대화합니다.
    """
    start_time = time.time()
    
    # 들어온 문장 배열 (예: 15개)
    raw_texts = request.texts
    texts_count = len(raw_texts)
    
    print(f"\n[AI_INFERENCE] 📦 대량 Batch 요청 접수 | 문장 개수: {texts_count}개")
    
    if texts_count == 0:
        return {"results": []}

    try:
        # 🔥 [핵심] for문을 돌리지 않고 배열 전체를 파이프라인으로 던집니다!
        # (단, pipeline.py 파일 안에서 run_batch 라는 함수를 만들어 배열을 받도록 수정해야 합니다)
        batch_results = pipeline.run_batch(raw_texts)
        
    except Exception as e:
        print(f"[AI_INFERENCE] ❌ Batch 내부 연산 중 에러 발생: {str(e)}")
        # 에러 발생 시 모두 안전하다고 처리해서 프론트엔드가 뻗는 걸 방지
        batch_results = [{"isInappropriate": False, "stage": "error", "reason": "서버 연산 에러"}] * texts_count

    latency_ms = (time.time() - start_time) * 1000
    
    # 50개를 쐈는데 2초(2000ms)가 걸렸다면, 1개당 40ms밖에 안 걸린 엄청난 효율입니다!
    print(f"[AI_INFERENCE] ⏱️ Batch({texts_count}개) 추론 완료 소요 시간: {latency_ms:.2f}ms")
    print(f"[AI_INFERENCE] 📊 1개당 평균 처리 시간: {(latency_ms / texts_count):.2f}ms")
    
    return {"results": batch_results}