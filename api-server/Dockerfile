FROM python:3.11-slim

WORKDIR /app

# 필수 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# 실행 (기본적으로 app/main.py 위치를 uvicorn이 찾을 수 있게 설정)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]