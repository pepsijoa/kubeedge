# 라즈베리파이(ARM) 호환 베이스 이미지
FROM python:3.9-slim-buster

# 작업 디렉토리 생성
WORKDIR /app

# [중요] DHT 센서 라이브러리 구동을 위한 시스템 패키지 설치
# gcc, libc-dev는 빌드용, libgpiod2는 GPIO 제어용
RUN apt-get update && apt-get install -y \
    gcc \
    libc-dev \
    libgpiod2 \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY mapper.py .

# 실행 (로그 즉시 출력을 위해 -u 옵션 사용)
CMD ["python", "-u", "mapper.py"]