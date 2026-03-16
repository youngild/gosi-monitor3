FROM python:3.12-slim

# 시스템 의존성 + 한국어 폰트 설치
RUN apt-get update && apt-get install -y \
    fonts-noto-cjk \
    wget \
    ca-certificates \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Playwright Chromium 설치
RUN playwright install chromium && playwright install-deps chromium

# 소스 복사
COPY . .

# 데이터 디렉토리 생성
RUN mkdir -p data/files/mohw data/files/hira

EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:${PORT:-5000} \
    --workers 1 \
    --threads 2 \
    --timeout 300 \
    --graceful-timeout 120 \
    "web.app:create_app()"
