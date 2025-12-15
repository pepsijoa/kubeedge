#!/bin/bash
# Dashboard를 Cloud 노드에서 직접 실행하는 스크립트

echo "=== KubeEdge Dashboard 시작 (Cloud 노드) ==="

# Python 가상환경 확인
if [ ! -d "venv" ]; then
    echo "가상환경 생성 중..."
    python3 -m venv venv
fi

source venv/bin/activate

# 필요한 패키지 설치
echo "패키지 설치 중..."
pip install -q flask flask-socketio flask-cors kubernetes

# Dashboard 실행
echo "Dashboard 시작..."
python3 dashboard.py

