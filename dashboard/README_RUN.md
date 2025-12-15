# Dashboard 실행 가이드 (Cloud 노드)

## 방법 1: 직접 실행 (테스트용)

```bash
cd /home/kkw/kubeEdge/dashboard
./run_dashboard.sh
```

접속: http://192.168.0.5:5000

종료: Ctrl+C

---

## 방법 2: 백그라운드 실행 (nohup)

```bash
cd /home/kkw/kubeEdge/dashboard

# 가상환경 및 패키지 설치 (최초 1회)
python3 -m venv venv
source venv/bin/activate
pip install flask flask-socketio flask-cors kubernetes

# 백그라운드 실행
nohup python3 dashboard.py > dashboard.log 2>&1 &

# 프로세스 확인
ps aux | grep dashboard.py

# 로그 확인
tail -f dashboard.log

# 종료
pkill -f dashboard.py
```

---

## 방법 3: systemd 서비스 등록 (자동 시작)

```bash
# 1. 서비스 파일 복사
sudo cp /home/kkw/kubeEdge/dashboard/kubeedge-dashboard.service /etc/systemd/system/

# 2. 서비스 활성화 및 시작
sudo systemctl daemon-reload
sudo systemctl enable kubeedge-dashboard
sudo systemctl start kubeedge-dashboard

# 3. 상태 확인
sudo systemctl status kubeedge-dashboard

# 4. 로그 확인
sudo journalctl -u kubeedge-dashboard -f

# 5. 중지
sudo systemctl stop kubeedge-dashboard
```

---

## Kubernetes Pod 삭제 (Cloud에서 직접 실행하므로 불필요)

```bash
kubectl delete deployment dashboard
```

---

## 접속 정보

- **URL**: http://192.168.0.5:5000
- **외부 접속 (같은 네트워크)**: http://192.168.0.5:5000
- **NodePort (불필요)**: 삭제 가능

---

## 동작 확인

1. Dashboard 실행 후 로그 확인:
   - "Kubernetes 설정 로드 완료"
   - "Device Watch 스레드 시작됨"
   - "이벤트 수신: MODIFIED - Device: my-doorlock"
   - "상태 업데이트 [my-doorlock]: Temperature=23.0"

2. 브라우저 접속:
   - Device 카드 표시
   - 온도: 23.0 °C
   - 30초마다 자동 업데이트 (WebSocket)

3. 비밀번호 변경 테스트:
   - Dashboard에서 새 비밀번호 입력
   - Edge의 Mapper가 변경 감지
   - 로그 확인: "비밀번호 변경 요청: 0000 -> XXXX"
