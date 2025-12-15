# KubeEdge Smart Doorlock System Specification

## 1. System Overview

이 프로젝트는 KubeEdge 프레임워크를 사용하여 Cloud(Raspberry Pi Master)와 Edge(Raspberry Pi Worker) 간의 양방향 통신 시스템을 구축합니다.

- **Cloud Node**: Kubernetes Master + CloudCore. 데이터 모니터링 및 명령 하달(Dashboard).
- **Edge Node**: EdgeCore + MQTT Broker. 물리적 장치(Sensor/Actuator) 제어.

### 핵심 기능

| 기능 | 설명 | 데이터 흐름 |
|------|------|-------------|
| **Monitoring** | Edge의 센서 데이터를 Cloud 대시보드에서 확인 | Edge → Cloud |
| **Control** | Cloud에서 Device Twin의 Desired 값을 변경하여 Edge의 파이썬 변수(비밀번호)를 실시간 변경 | Cloud → Edge |

---

## 2. KubeEdge 컴포넌트 및 아키텍처

### 2.1 사용된 KubeEdge 컴포넌트

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLOUD SIDE                                     │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐ │
│  │  Kubernetes     │    │   CloudCore     │    │     Dashboard           │ │
│  │  API Server     │◄──►│                 │    │  (Flask + WebSocket)    │ │
│  │                 │    │  ┌───────────┐  │    │                         │ │
│  │  Device CRD     │    │  │CloudHub   │  │    │  - Watch Device CRD     │ │
│  │  - DeviceModel  │    │  │(WebSocket)│  │    │  - 실시간 상태 표시      │ │
│  │  - Device       │    │  └───────────┘  │    │  - Desired 값 변경      │ │
│  └─────────────────┘    │  ┌───────────┐  │    └─────────────────────────┘ │
│                         │  │DeviceCntrl│  │                                │
│                         │  │ler       │  │                                │
│                         │  └───────────┘  │                                │
│                         └────────┬────────┘                                │
└──────────────────────────────────┼─────────────────────────────────────────┘
                                   │ WebSocket (10000/10002)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EDGE SIDE                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                           EdgeCore                                   │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────────┐ │   │
│  │  │  EdgeHub  │  │ DeviceTwin│  │  EventBus │  │    MetaManager    │ │   │
│  │  │           │◄─┤           │◄─┤           │  │                   │ │   │
│  │  │ Cloud연결 │  │ Twin동기화│  │MQTT브로커 │  │ 메타데이터 저장   │ │   │
│  │  └───────────┘  └───────────┘  └─────┬─────┘  └───────────────────┘ │   │
│  └──────────────────────────────────────┼──────────────────────────────┘   │
│                                         │ MQTT (localhost:1884)            │
│                                         ▼                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Mapper (doorlock-mapper)                          │   │
│  │  - DHT11 온도 센서 읽기                                              │   │
│  │  - MQTT로 Cloud에 온도 보고 (Reported)                               │   │
│  │  - Cloud에서 비밀번호 변경 수신 (Desired → Delta)                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 컴포넌트별 역할

| 컴포넌트 | 위치 | 역할 |
|----------|------|------|
| **CloudCore** | Cloud | Edge 노드와의 통신 관리, Device CRD 동기화 |
| **CloudHub** | Cloud (CloudCore 내부) | WebSocket/QUIC을 통한 Edge 연결 관리 |
| **DeviceController** | Cloud (CloudCore 내부) | Device CRD 변경 감시 및 Edge로 전파 |
| **EdgeCore** | Edge | Edge 측 핵심 컴포넌트, 모든 Edge 모듈 관리 |
| **EdgeHub** | Edge (EdgeCore 내부) | Cloud와의 WebSocket 연결 유지 |
| **DeviceTwin** | Edge (EdgeCore 내부) | Device Twin 상태 관리, MQTT 메시지 처리 |
| **EventBus** | Edge (EdgeCore 내부) | MQTT 브로커 인터페이스 (내부/외부 모드 지원) |
| **Mapper** | Edge (Pod) | 실제 디바이스(센서/액추에이터)와 통신 |

### 2.3 MQTT 토픽 규격 (KubeEdge 공식)

```
EventBus가 구독하는 토픽:
- $hw/events/upload/#
- $hw/event/node/+/membership/get
- $hw/events/device/+/state/update
- $hw/event/device/+/twin/+

Mapper가 사용하는 토픽:
- 구독 (Cloud→Edge, Desired 변경 수신):
  $hw/events/device/{device_name}/twin/update/delta
  
- 발행 (Edge→Cloud, Reported 값 전송):
  $hw/events/device/{device_name}/twin/update
```

### 2.4 Device Twin 페이로드 형식

```json
// Edge → Cloud (Reported 값 업데이트)
{
    "event_id": "temp-update-1702450000",
    "timestamp": 1702450000000,
    "twin": {
        "temperature": {
            "actual": {
                "value": "25.5",
                "metadata": {
                    "timestamp": 1702450000000
                }
            },
            "metadata": {
                "type": "float"
            }
        }
    }
}

// Cloud → Edge (Delta 메시지 - Desired 변경 시)
{
    "delta": {
        "password": "1234"
    }
}
```

---

## 3. Kubernetes Resources (CRD)

Cloud에 배포되어야 할 Device Model과 Device 정의입니다.

### A. Device Model (`deploy/device-model.yaml`)

```yaml
apiVersion: devices.kubeedge.io/v1beta1
kind: DeviceModel
metadata:
  name: doorlock-model
  namespace: default
spec:
  properties:
    - name: temperature
      description: Current temperature reading from sensor
      type: FLOAT
      accessMode: ReadOnly
      unit: Celsius
    - name: password
      description: Doorlock password for access control
      type: STRING
      accessMode: ReadWrite
```

### B. Device Instance (`deploy/device-instance.yaml`)

```yaml
apiVersion: devices.kubeedge.io/v1beta1
kind: Device
metadata:
  name: my-doorlock
  namespace: default
spec:
  deviceModelRef:
    name: doorlock-model
  nodeName: raspberrypi-temp  # Edge 노드 호스트네임
  properties:
    - name: temperature
      desired:
        value: "0.0"
        metadata:
          type: string
      reportToCloud: true
      collectCycle: 30000000000  # 30초 (나노초)
      reportCycle: 30000000000
    - name: password
      desired:
        value: "0000"
        metadata:
          type: string
      reportToCloud: true
```

### C. RBAC 설정 (`deploy/dashboard-rbac.yaml`)

Dashboard가 Device CRD에 접근하기 위한 권한:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: device-reader
rules:
  - apiGroups: ["devices.kubeedge.io"]
    resources: ["devices", "devicemodels", "devices/status"]
    verbs: ["get", "list", "watch", "patch", "update"]
```

---

## 4. Edge Side Application (Python Mapper)

Edge 라즈베리파이에서 도커 컨테이너로 실행되는 애플리케이션입니다.

### 역할
- 실제 도어락 로직 수행 및 KubeEdge DeviceTwin과의 MQTT 통신

### MQTT 설정
```python
MQTT_BROKER = "localhost"
MQTT_PORT = 1884  # EdgeCore EventBus 내부 브로커 포트
DEVICE_NAME = "my-doorlock"

# 구독 (Cloud → Edge): Desired 값 변경 수신
SUBSCRIBE_TOPIC = "$hw/events/device/my-doorlock/twin/update/delta"

# 발행 (Edge → Cloud): Reported 값 전송
PUBLISH_TOPIC = "$hw/events/device/my-doorlock/twin/update"
```

### 핵심 로직

1. **온도 센서 읽기** (DHT11)
   ```python
   dht_device = adafruit_dht.DHT11(board.D24)
   temperature = dht_device.temperature
   ```

2. **Cloud로 온도 보고** (30초 주기)
   ```python
   payload = {
       "event_id": f"temp-update-{timestamp}",
       "timestamp": timestamp_ms,
       "twin": {
           "temperature": {
               "actual": {
                   "value": str(temperature),
                   "metadata": {"timestamp": timestamp_ms}
               },
               "metadata": {"type": "float"}
           }
       }
   }
   client.publish(PUBLISH_TOPIC, json.dumps(payload))
   ```

3. **비밀번호 변경 수신**
   ```python
   # Delta 메시지 파싱
   if 'delta' in payload:
       if 'password' in payload['delta']:
           current_password = payload['delta']['password']
   ```

### 배포 (`deploy/mapper-deploy.yaml`)
```yaml
spec:
  nodeName: raspberrypi-temp
  hostNetwork: true  # localhost:1884 접근 필요
  containers:
  - name: mapper
    image: kkw4278/kubeedge-mapper:v13
    securityContext:
      privileged: true  # GPIO 접근 필요
    volumeMounts:
    - name: dev
      mountPath: /dev
```

---

## 5. Cloud Side Application (Dashboard)

Cloud 라즈베리파이에서 실행되는 웹 대시보드입니다.

### 역할
- Kubernetes API를 Watch하여 Device 상태 실시간 모니터링
- WebSocket으로 웹 UI에 푸시
- REST API로 Desired 값 변경 (비밀번호 제어)

### 기술 스택
- **Framework**: Flask + Flask-SocketIO
- **K8s Client**: `kubernetes` Python library
- **인증**: ServiceAccount + ClusterRole (RBAC)

### 핵심 로직

1. **Device Watch**
   ```python
   api = client.CustomObjectsApi()
   w = watch.Watch()
   
   for event in w.stream(
       api.list_namespaced_custom_object,
       group="devices.kubeedge.io",
       version="v1beta1",
       namespace="default",
       plural="devices"
   ):
       # ADDED, MODIFIED, DELETED 이벤트 처리
       device_obj = event['object']
       twins_data = parse_device_twins(device_obj)
   ```

2. **Twin 데이터 파싱**
   - `spec.properties[].desired.value` → Desired 값
   - `status.twins[].reported.value` → Reported 값 (Edge에서 보고됨)

3. **Desired 값 변경 API**
   ```python
   @app.route('/api/device/<name>/update/<property>/<value>', methods=['POST'])
   def update_device_property(name, property, value):
       # spec.properties[].desired.value 업데이트
       api.patch_namespaced_custom_object(...)
   ```

### 배포 (`deploy/dashboard-deploy.yaml`)
```yaml
spec:
  serviceAccountName: dashboard-sa  # RBAC 권한 필요
  nodeName: raspberrypi
  hostNetwork: true
  containers:
  - name: dashboard
    image: kkw4278/kubeedge-dashboard:v9
    ports:
    - containerPort: 5000
```

---

## 6. Deployment Strategy

모든 애플리케이션은 Docker Container 및 Kubernetes Pod로 배포됩니다.

### 아키텍처
- **Platform**: linux/arm64 (Raspberry Pi 호환)
- **Container Runtime**: containerd (K3s/KubeEdge 호환)

### 배포 순서

```bash
# 1. Device Model 배포
kubectl apply -f deploy/device-model.yaml

# 2. Device Instance 배포
kubectl apply -f deploy/device-instance.yaml

# 3. Dashboard RBAC 배포
kubectl apply -f deploy/dashboard-rbac.yaml

# 4. Dashboard 배포
kubectl apply -f deploy/dashboard-deploy.yaml

# 5. Mapper 배포
kubectl apply -f deploy/mapper-deploy.yaml
```

### 확인 명령어

```bash
# Pod 상태 확인
kubectl get pods -o wide

# Device 상태 확인
kubectl get devices.devices.kubeedge.io

# Device 상세 정보 (Twins 포함)
kubectl get devices.devices.kubeedge.io my-doorlock -o yaml

# Mapper 로그 확인
kubectl logs -f deploy/doorlock-mapper

# Dashboard 로그 확인
kubectl logs -f deploy/dashboard
```

### 네트워크 설정
- **Dashboard**: NodePort 30000 → 외부 접근 허용
- **Mapper**: hostNetwork=true → EdgeCore MQTT 접근

---

## 7. 트러블슈팅

### 7.1 온도 데이터가 Cloud에 반영되지 않는 경우

**원인 분석**:
1. MQTT 브로커 연결 실패
2. 페이로드 형식 불일치
3. EdgeCore ↔ CloudCore 연결 끊김

**확인 방법**:
```bash
# Edge 노드에서 MQTT 브로커 확인
ss -tunlp | grep 1884

# EdgeCore 로그 확인
journalctl -u edgecore -f

# Mapper 로그 확인
kubectl logs -f deploy/doorlock-mapper
```

**해결책**:
- `edgecore.yaml`의 `modules.eventBus.mqttMode` 확인 (0=internal, 1=both, 2=external)
- Mapper의 MQTT_PORT가 EdgeCore 설정과 일치하는지 확인
- 페이로드의 `twin.<property>.actual.value` 형식 확인

### 7.2 비밀번호 변경이 Edge에 전달되지 않는 경우

**확인 방법**:
```bash
# Device의 Desired 값 확인
kubectl get devices.devices.kubeedge.io my-doorlock -o jsonpath='{.spec.properties}'

# EdgeCore devicetwin 로그 확인
journalctl -u edgecore | grep -i twin
```

**해결책**:
- Dashboard의 patch 요청이 성공했는지 확인
- EdgeCore와 CloudCore 간 WebSocket 연결 상태 확인

### 7.3 EdgeCore 설정 예시

```yaml
# /etc/kubeedge/config/edgecore.yaml
modules:
  eventBus:
    enable: true
    mqttMode: 0  # 0=internal (내장 브로커 사용)
    mqttQOS: 0
    mqttRetain: false
    mqttSessionQueueSize: 100
```

---

## 8. 참고 자료

### KubeEdge 공식 문서
- [DeviceTwin 아키텍처](https://kubeedge.io/docs/architecture/edge/devicetwin/)
- [EventBus 토픽 규격](https://kubeedge.io/docs/architecture/edge/eventbus/)
- [Mapper Framework](https://kubeedge.io/docs/developer/mapper-framework)
- [CloudCore/EdgeCore 설정](https://kubeedge.io/docs/setup/config)

### MQTT 토픽 규격
| 토픽 | 방향 | 용도 |
|------|------|------|
| `$hw/events/device/+/twin/update/delta` | Cloud→Edge | Desired 값 변경 알림 |
| `$hw/events/device/+/twin/update` | Edge→Cloud | Reported 값 업데이트 |
| `$hw/events/device/+/state/update` | 양방향 | 디바이스 상태 업데이트 |

### Device Twin 데이터 흐름
1. **Cloud에서 Desired 변경** → K8s API → DeviceController → CloudHub → EdgeHub → DeviceTwin → EventBus → MQTT(delta 토픽) → Mapper
2. **Mapper에서 Reported 업데이트** → MQTT → EventBus → DeviceTwin → EdgeHub → CloudHub → DeviceController → K8s API (status.twins 업데이트)