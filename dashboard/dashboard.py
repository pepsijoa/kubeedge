#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KubeEdge Cloud Side Application - Dashboard
Cloud 노드에서 실행되어 Kubernetes API를 통해 Device 상태를 모니터링하는 웹 대시보드
"""

from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from kubernetes import client, config, watch
import threading
import logging
import json
import time
import subprocess
import shlex
from datetime import datetime
import pytz

# 한국 시간대 설정 (UTC+9)
KST = pytz.timezone('Asia/Seoul')

# 로깅 설정 - 한국 시간대 적용
class KSTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime('%Y-%m-%d %H:%M:%S %Z')

handler = logging.StreamHandler()
handler.setFormatter(KSTFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.propagate = False

# Flask 앱 초기화
app = Flask(__name__)
app.config['SECRET_KEY'] = 'kubeedge-dashboard-secret'
# CORS 설정 강화
CORS(app, resources={r"/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})
socketio = SocketIO(app, cors_allowed_origins="*")

# KubeEdge Device CRD 정보
DEVICE_GROUP = "devices.kubeedge.io"
DEVICE_VERSION = "v1beta1"
DEVICE_PLURAL = "devices"
DEVICE_NAMESPACE = "default"

# Edge MQTT 브로커 정보
EDGE_NODE_IP = "192.168.0.12"
EDGE_NODE_USER = "kkw"
EDGE_MQTT_PORT = 1884
DEVICE_NAME = "my-doorlock"

# 현재 디바이스 상태 저장 (다중 디바이스 지원)
# Key: device_name, Value: device_info dict
devices_cache = {}


def load_kubernetes_config():
    """Kubernetes 설정 로드 (클러스터 내부 또는 외부)"""
    try:
        # Pod 내부에서 실행되는 경우 (ServiceAccount 사용)
        config.load_incluster_config()
        
        # SSL 검증 비활성화 (개발 환경용)
        c = client.Configuration.get_default_copy()
        c.verify_ssl = False
        client.Configuration.set_default(c)
        
        logger.info("Kubernetes In-Cluster 설정 로드 완료 (SSL 검증 비활성화)")
    except config.ConfigException:
        try:
            # 로컬 개발 환경 (kubeconfig 사용)
            config.load_kube_config()
            logger.info("Kubernetes Kubeconfig 설정 로드 완료")
        except Exception as e:
            logger.error(f"Kubernetes 설정 로드 실패: {e}")
            raise


def publish_delta_via_ssh(device_name, property_name, desired_value, property_type="string"):
    """SSH를 통해 Edge 노드에서 MQTT Delta 메시지 발행"""
    
    # Delta 메시지 토픽
    topic = f"$hw/events/device/{DEVICE_NAMESPACE}/{device_name}/twin/update/delta"
    
    # Delta 메시지 페이로드 구성
    delta_payload = {
        property_name: {
            "expected": {
                "value": desired_value,
                "metadata": {
                    "type": property_type
                }
            },
            "actual": {
                "value": "",
                "metadata": {
                    "type": property_type
                }
            }
        }
    }
    
    payload_json = json.dumps(delta_payload)
    
    # mosquitto_pub 명령 (따옴표 이스케이핑)
    mqtt_cmd = f"mosquitto_pub -h 127.0.0.1 -p {EDGE_MQTT_PORT} -t '{topic}' -m '{payload_json}'"
    
    # SSH를 통한 실행 (호스트 키 검증 비활성화)
    ssh_command = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        f"{EDGE_NODE_USER}@{EDGE_NODE_IP}",
        mqtt_cmd
    ]
    
    logger.info(f"→ SSH를 통한 MQTT Delta 발행 시도")
    logger.info(f"  Payload: {payload_json}")
    
    try:
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            logger.info(f"✓ Delta 메시지 발행 성공: {property_name}={desired_value}")
            return True
        else:
            logger.error(f"✗ SSH 명령 실패 (코드: {result.returncode})")
            if result.stderr:
                logger.error(f"  stderr: {result.stderr.strip()}")
            if result.stdout:
                logger.error(f"  stdout: {result.stdout.strip()}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("SSH 명령 타임아웃")
        return False
    except Exception as e:
        logger.error(f"Delta 메시지 발행 중 오류: {e}")
        return False


def parse_device_twins(device_obj):
    """Device 객체에서 Twin 데이터 파싱 (v1beta1 대응)"""
    try:
        result = {}
        
        # 1. Spec에서 Desired 값 파싱
        spec = device_obj.get('spec', {})
        properties = spec.get('properties', [])
        for prop in properties:
            name = prop.get('name')
            desired = prop.get('desired', {}).get('value', '')
            result[name] = {
                'desired': desired,
                'reported': 'unknown'
            }
            
        # 2. Status에서 Reported 값 파싱
        status = device_obj.get('status', {})
        twins = status.get('twins', [])
        
        for twin in twins:
            name = twin.get('propertyName')
            reported_obj = twin.get('reported', {})
            reported = reported_obj.get('value', '') if reported_obj else ''
            
            # 빈 문자열이면 'Mapper 미보고'로 표시
            if not reported:
                reported = 'Mapper 미보고'
            
            if name in result:
                result[name]['reported'] = reported
            else:
                result[name] = {
                    'desired': 'unknown',
                    'reported': reported
                }
        
        return result
    except Exception as e:
        logger.error(f"Twin 데이터 파싱 오류: {e}")
        return {}


def watch_devices():
    """Kubernetes API를 통해 Device 리소스를 Watch"""
    logger.info("=== Device Watch 시작 ===")
    
    while True:
        try:
            # CustomObjectsApi 생성
            api = client.CustomObjectsApi()
            
            # Watch 스트림 생성
            w = watch.Watch()
            
            logger.info(f"Watch 대상: {DEVICE_GROUP}/{DEVICE_VERSION} {DEVICE_PLURAL} (namespace: {DEVICE_NAMESPACE})")
            
            # Device 리소스 Watch
            for event in w.stream(
                api.list_namespaced_custom_object,
                group=DEVICE_GROUP,
                version=DEVICE_VERSION,
                namespace=DEVICE_NAMESPACE,
                plural=DEVICE_PLURAL,
                timeout_seconds=0  # 무한 대기
            ):
                event_type = event['type']  # ADDED, MODIFIED, DELETED
                device_obj = event['object']
                
                device_name = device_obj.get('metadata', {}).get('name', 'unknown')
                
                logger.info(f"이벤트 수신: {event_type} - Device: {device_name}")
                
                # MODIFIED 이벤트 처리 (상태 변경)
                if event_type in ['ADDED', 'MODIFIED']:
                    # Twin 데이터 파싱
                    twins_data = parse_device_twins(device_obj)
                    
                    # 디바이스 정보 구성 (한국 시간 적용)
                    current_device = {
                        'name': device_name,
                        'last_update': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST'),
                        'twins': twins_data,
                        'password': 'unknown',
                        'temperature': 'unknown'
                    }
                    
                    # 주요 속성 추출
                    if 'password' in twins_data:
                        current_device['password'] = twins_data['password'].get('reported', 
                                                                              twins_data['password'].get('desired', 'unknown'))
                    
                    if 'temperature' in twins_data:
                        current_device['temperature'] = twins_data['temperature'].get('reported', 'unknown')
                    
                    # 캐시 업데이트
                    devices_cache[device_name] = current_device
                    
                    logger.info(f"상태 업데이트 [{device_name}]: Password={current_device['password']}, "
                               f"Temperature={current_device['temperature']}")
                    
                    # WebSocket을 통해 클라이언트에게 실시간 전송
                    socketio.emit('device_update', current_device)
                
                # DELETED 이벤트 처리
                elif event_type == 'DELETED':
                    if device_name in devices_cache:
                        del devices_cache[device_name]
                        socketio.emit('device_delete', {'name': device_name})
                        logger.info(f"디바이스 삭제됨: {device_name}")
            
        except client.exceptions.ApiException as e:
            logger.error(f"Kubernetes API 오류: {e}")
            logger.error(f"Status: {e.status}, Reason: {e.reason}")
            logger.error("ServiceAccount 권한을 확인하세요. (ClusterRole: devices.kubeedge.io 리소스 접근 필요)")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Watch 중 오류 발생: {e}")
            import traceback
            logger.error(traceback.format_exc())
            time.sleep(5)
        
        logger.info("Watch 재시작 중...")


def start_watch_thread():
    """백그라운드 스레드에서 Watch 실행"""
    watch_thread = threading.Thread(target=watch_devices, daemon=True)
    watch_thread.start()
    logger.info("Device Watch 스레드 시작됨")


@app.route('/')
def index():
    """메인 대시보드 페이지"""
    return render_template('index.html')


@app.route('/api/devices')
def get_all_devices():
    """모든 디바이스 상태 목록 API"""
    return jsonify(list(devices_cache.values()))


@app.route('/api/device/<device_name>/update/<property_name>/<value>', methods=['POST'])
def update_device_property(device_name, property_name, value):
    """특정 디바이스 속성 업데이트 API (SSH를 통한 MQTT Delta 메시지 발행만 수행)"""
    try:
        logger.info(f"속성 업데이트 요청: {device_name}.{property_name} = {value}")
        
        # SSH를 통해 Edge에서 Delta 메시지 발행
        mqtt_success = publish_delta_via_ssh(device_name, property_name, value)
        
        if mqtt_success:
            logger.info(f"✓ MQTT Delta 발행 성공: {device_name}.{property_name}={value}")
            return jsonify({
                "status": "success", 
                "message": f"MQTT Delta published for {device_name}.{property_name}",
                "mqtt_published": True,
                "value": value
            })
        else:
            logger.error(f"✗ MQTT Delta 발행 실패: {device_name}.{property_name}={value}")
            return jsonify({
                "status": "error",
                "message": "Failed to publish MQTT Delta message",
                "mqtt_published": False
            }), 500
        
    except Exception as e:
        logger.error(f"디바이스 업데이트 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


@socketio.on('connect')
def handle_connect():
    """클라이언트 WebSocket 연결"""
    logger.info(f"클라이언트 연결됨")
    # 연결 시 현재 상태 전송 (모든 디바이스 정보)
    for device in devices_cache.values():
        emit('device_update', device)


@socketio.on('disconnect')
def handle_disconnect():
    """클라이언트 WebSocket 연결 해제"""
    logger.info(f"클라이언트 연결 해제됨")


def main():
    """메인 함수"""
    logger.info("=== KubeEdge Dashboard 시작 ===")
    
    try:
        # Kubernetes 설정 로드
        load_kubernetes_config()
        
        # Device Watch 스레드 시작
        start_watch_thread()
        
        # Flask 서버 시작
        logger.info("Flask 서버 시작: http://0.0.0.0:5000")
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
        
    except KeyboardInterrupt:
        logger.info("사용자에 의해 종료됨")
    except Exception as e:
        logger.error(f"애플리케이션 오류: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.info("=== Dashboard 종료 ===")


if __name__ == "__main__":
    main()
