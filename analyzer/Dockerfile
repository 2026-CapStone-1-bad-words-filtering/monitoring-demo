FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

# 필수 도구 설치
RUN apt-get update && apt-get install -y xvfb && apt-get clean

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

# [핵심] xvfb-run을 쓰지 않고 직접 제어합니다.
# 1. 혹시 남았을지 모를 X 서버 락 파일을 지웁니다.
# 2. Xvfb를 디스플레이 :99번으로 띄웁니다.
# 3. uvicorn을 실행합니다.
CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb :99 -screen 0 1280x1024x24 -ac & sleep 2 && uvicorn app.main:app --host 0.0.0.0 --port 8000"]