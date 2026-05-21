# 1. 가벼운 파이썬 베이스 이미지 사용 (이미지 용량 최적화)
FROM python:3.11-slim

# 2. 작업 디렉토리 설정
WORKDIR /app

# 3. 필수 시스템 패키지 설치 (빌드 및 의존성 라이브러리)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 4. 의존성 파일 복사 및 설치
# inference-engine 폴더 안에 해당 서비스 전용 requirements.txt가 있어야 합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5. 소스 코드 복사
COPY ./app ./app

# 6. AI 모델은 이미지를 빌드할 때 포함하지 말고, 
#    나중에 도커 컨테이너 실행 시 볼륨(Volume)으로 마운트하는 것을 추천합니다.
#    (아래는 이미지 내부에 포함하고 싶을 경우 복사하는 코드입니다)
# COPY ./models ./models 

# 7. 포트 개방
EXPOSE 8080

# 8. 서버 실행 (FastAPI/Uvicorn)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]