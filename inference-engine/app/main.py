import os
from fastapi import FastAPI
from .schemas import DetectRequest
from .pipeline import ModerationPipeline

app = FastAPI(title="AI Inference Engine")

# 도커 내부에서 마운트될 모델 폴더 경로 (기본값 설정)
MODEL_DIR = os.getenv("MODEL_DIR", "/app/models/bert_profanity_model")

# 서버 켜질 때 딱 한 번만 파이프라인(모델 포함) 적재
pipeline = ModerationPipeline(model_dir=MODEL_DIR)

@app.post("/detect")
async def detect_text(request: DetectRequest):
    """api-server가 던져준 텍스트를 검열하고 결과를 반환"""
    return pipeline.run(request.content)