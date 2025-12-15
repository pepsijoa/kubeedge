FROM python:3.9-slim

WORKDIR /app

# SSH 클라이언트 설치 (MQTT Delta 발행용)
RUN apt-get update && apt-get install -y openssh-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# [중요] 현재 폴더의 모든 내용(templates 폴더 포함)을 컨테이너로 복사
COPY . .

# Flask 실행
CMD ["python", "-u", "dashboard.py"]