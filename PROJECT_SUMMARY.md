# KubeEdge 스마트 도어락 프로젝트 총정리 (2025-12-14)

본 문서는 KubeEdge 기반 스마트 도어락 시스템 구현의 전체 여정을 정리합니다. 아래에 어떤 KubeEdge 기능을 참고해 구현했는지, 그리고 가장 어려웠던 지점을 포함합니다.

---

## 1. 프로젝트 개요

- 목적: 클라우드(대시보드)에서 도어락 비밀번호를 변경하면 엣지(라즈베리파이)의 매퍼가 이를 반영해 파일로 저장하고, 현장에서 입력된 비밀번호가 맞으면 아두이노로 도어락 개폐를 제어
- 구성요소:
  - Cloud: CloudCore + Dashboard(Flask) + Kubernetes
  - Edge: EdgeCore + Mapper(Python) + 로컬 MQTT 브로커(1884) + Arduino(Serial)
  - Device Model/Instance: KubeEdge DeviceTwin로 상태/설정 동기화

---

## 2. 아키텍처

```
Cloud Dashboard (REST) ──> DeviceTwin Update ──> MQTT (Cloud→Edge)
                                         │                      │
                                         ▼                      ▼
                               KubeEdge CloudCore         EdgeCore (EdgeHub)
                                                             │
                                                             ▼
                                    Mapper (Python, MQTT subscribe/publish)
                                         │                 │
                                  save to /tmp/password    report back to Twin
                                         │                 │
                                      CLI verify         Arduino Serial (/dev/ttyACM0)
                                         │
                                   Doorlock open/close
```

- Mapper 주요 토픽
  - Subscribe(Delta): `$hw/events/device/{namespace}/{device_name}/twin/update/delta`
  - Publish(Update):  `$hw/events/device/{namespace}/{device_name}/twin/update`
- 비밀번호 저장: `/tmp/doorlock_password.txt` (v22 기준)
- 배포: `deploy/mapper-deploy.yaml`로 Edge 노드에 매퍼 Pod 배포

---

## 3. KubeEdge 참조 및 구현 포인트

아래 항목은 실제 구현에서 직접 참고/활용한 KubeEdge 기능이나 리소스입니다.

- DeviceTwin (상태/설정 동기화)
  - 참고: KubeEdge의 Device CRD와 Twin 구조
  - 구현:
    - Cloud에서 비밀번호 변경 → DeviceTwin desired 업데이트
    - Edge 매퍼는 `update/delta` 토픽을 구독하여 변경 감지
    - 변경된 비밀번호를 파일(`/tmp/doorlock_password.txt`)로 저장
    - 매퍼는 변경 사항을 `twin/update` 토픽으로 reported에 반영

- MQTT 이벤트 채널
  - 토픽 규칙: `$hw/events/device/{namespace}/{device_name}/twin/update/delta` / `.../update`
  - Payload 포맷: `twin` 배열의 `propertyName`/`desired`/`reported` 구조를 유지
  - 로컬 브로커: `localhost:1884` 사용 (Edge 환경)

- Device Model / Device Instance
  - 파일 위치: `deploy/device-model.yaml`, `deploy/device-instance.yaml`
  - 속성 예: `password`, `temperature`
  - 흐름: Cloud에서 `password` desired 변경 시 Edge reported가 반영되도록 매퍼가 보고

- CloudCore / EdgeCore
  - CloudCore: Kubernetes와 연동하여 DeviceTwin API 경유
  - EdgeCore: EdgeHub가 MQTT 브로커와 상호작용하여 매퍼와 통신

- 매퍼 배포 (Kubernetes)
  - `deploy/mapper-deploy.yaml` 참고
  - `image: kkw4278/kubeedge-mapper:v22`
  - 필요 마운트: `/dev`, `/sys` (Serial/센서 접근)
  - v21 이전에는 `/var/lib/doorlock` hostPath를 사용했으나 v21부터 제거(경로를 `/tmp`로 단순화)

- Dashboard (Cloud)
  - Flask 기반 API: `POST /api/device/{device}/update/password/{NEWPASS}`
  - 수행: DeviceTwin desired 변경 → MQTT 발행 → Edge 반영 확인

---

## 4. 버전 히스토리 (핵심 변화)

- v17–v18: Serial 지원 준비
  - `pyserial` 사용, `/dev/ttyACM0`에 `open`/`close` 명령 송신 함수 추가
  - CLI 비밀번호 입력 스레드 구현 (현장에서 입력시 unlock→lock 시퀀스)

- v19–v20: 비밀번호 파일 영속화 + 로깅 최적화
  - 파일 경로: `/var/lib/doorlock/password.txt`
  - hostPath 볼륨 추가로 Pod 재시작에도 유지
  - `LOG_LEVEL` 환경변수로 MQTT 로그 축소

- v21: 배포 간소화
  - 파일 경로 `/tmp/doorlock_password.txt`로 변경
  - hostPath 볼륨 제거 (설치/권한 부담 완화)
  - 배포 이미지: `kkw4278/kubeedge-mapper:v21`

- v22: 코드 정리 및 안정화
  - `ensure_password_dir()` 제거 ( `/tmp` 상수 경로)
  - 최종 배포 이미지: `kkw4278/kubeedge-mapper:v22`
  - 상태 확인: Pod Running, Twin 동기화 정상, 파일 저장 정상

---

## 5. 가장 어려웠던 부분

- DeviceTwin 동기화(Cloud↔Edge) 정합성 확보
  - 난점: MQTT 토픽/페이로드 정확한 포맷 유지, desired→reported 단계에서 race condition 및 타이밍 조정
  - 해결: Delta 구독 후 파일 저장→즉시 reported 업데이트, 로그 레벨 조정으로 잡음 감소

- MQTT/네트워크 경계
  - 난점: Edge 브로커(1884) 연결 안정성, 재시도/오프라인 상태에서의 복구
  - 해결: 연결 상태 플래그, 예외 처리 강화, 재구독 루틴 검토

- 컨테이너 권한과 디바이스 접근
  - 난점: `/dev/ttyACM0` 접근 권한, `/sys` 접근 필요, hostPath 마운트 설계
  - 해결: `volumeMounts`로 `/dev`/`/sys` 제공, Serial 예외 처리, 배포 시 권한 최소화

- 파일 영속성과 배포 복잡도 트레이드오프
  - 난점: `/var/lib/doorlock` hostPath는 영속에 유리하나 배포/권한 복잡도 증가
  - 해결: 요구 사항 변경에 따라 `/tmp`로 단순화(v21+), 운영 환경에 따라 선택 가능하도록 구조 유지

- DHT11 하드웨어 타임아웃
  - 난점: 센서 읽기 실패로 온도 보고가 간헐적으로 실패
  - 해결: 소프트웨어 레벨 재시도/스킵 로깅, 하드웨어 점검 필요로 분리

---

## 6. 현재 상태 (v22)

- Pod: `doorlock-mapper` (Running)
- 비밀번호 파일: `/tmp/doorlock_password.txt` (예: `9999`/`FINAL2024`로 검증됨)
- MQTT: 정상 (Cloud→Edge Delta, Edge→Cloud Update)
- Serial: 준비 완료 (하드웨어 연결 시 동작)
- 온도: DHT11 타임아웃 문제로 미보고/간헐 보고

---

## 7. 운영 및 검증 명령어

- 배포 상태 확인
```bash
kubectl get pods -l app=doorlock-mapper -o wide
kubectl rollout status deployment/doorlock-mapper
```

- 비밀번호 변경 (Cloud Dashboard)
```bash
curl -X POST http://<cloud-ip>:5000/api/device/my-doorlock/update/password/NEWPASS
```

- 엣지에서 파일 확인
```bash
ssh kkw@<edge-ip> "cat /tmp/doorlock_password.txt"
```

- Pod 내부 확인
```bash
kubectl exec -it $(kubectl get pod -l app=doorlock-mapper -o jsonpath='{.items[0].metadata.name}') -- cat /tmp/doorlock_password.txt
```

- 엣지에서 CLI 테스트 (독립)
```bash
ssh kkw@<edge-ip>
cd /home/kkw
python3 test_doorlock_cli.py
```

---

## 8. 다음 단계 제안

- 하드웨어 안정화: DHT11 센서 교체 혹은 드라이버 튜닝
- 영속성 정책 결정: 운영환경에 맞춰 `/var/lib` hostPath 복원 여부 선택
- Serial 핸드셰이크/피드백: 아두이노로부터 상태 응답 수신 로직 추가
- 경량 모니터링: 실패/재시도 지표를 Cloud에 집계하여 대시보드 노출

---

## 부록: 주요 파일

- `kubeEdge/mapper/mapper.py`: MQTT 구독/발행, 파일 저장, Serial 제어
- `kubeEdge/deploy/mapper-deploy.yaml`: 매퍼 배포 스펙
- `kubeEdge/deploy/device-model.yaml`, `device-instance.yaml`: DeviceTwin 정의
- `kubeEdge/dashboard/dashboard.py`: 비밀번호 변경 REST 엔드포인트

