FROM python:3.9-slim

# 작업 디렉토리 설정
WORKDIR /app

# 필요한 패키지 설치
# [중요] Raspberry Pi 4B (BCM2711) DHT 센서 라이브러리 구동을 위한 시스템 패키지
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libc-dev \
    libgpiod-dev \
    python3-dev \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY mapper.py .

# 로그 버퍼링 방지를 위한 python -u 옵션 사용
CMD ["python", "-u", "mapper.py"]
