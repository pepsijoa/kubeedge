#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KubeEdge Edge Side Application - Doorlock Mapper
Edge 노드에서 실행되어 MQTT를 통해 KubeEdge와 통신하는 도어락 애플리케이션
"""

import paho.mqtt.client as mqtt
import json
import time
import random
import logging
import board
import adafruit_dht
import serial
import os
from threading import Thread, Event
from datetime import datetime
import pytz

# 한국 시간대 설정 (UTC+9)
KST = pytz.timezone('Asia/Seoul')

# 로깅 설정 - CLI 모드 시 WARNING 이상만 출력
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')  # 환경변수로 제어 가능

# 한국 시간대 적용 로거 설정
class KSTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime('%Y-%m-%d %H:%M:%S %Z')

handler = logging.StreamHandler()
handler.setFormatter(KSTFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL))
logger.addHandler(handler)
logger.propagate = False

# 아두이노 Serial 포트 설정 (환경변수에서 읽기)
ARDUINO_PORT = os.getenv('ARDUINO_PORT', '/dev/ttyACM0')
ARDUINO_BAUD_RATE = int(os.getenv('ARDUINO_BAUD_RATE', '9600'))
arduino_serial = None  # Serial 연결 객체

# 센서 설정 (GPIO 27번)
dht_device = None
try:
    logger.info("DHT11 센서 초기화 시도...")
    dht_device = adafruit_dht.DHT11(board.D27)
    logger.info("✓ DHT11 센서 초기화 성공")
    
    # 프로그램 종료 시 자동으로 GPIO cleanup
    import atexit
    def cleanup_gpio():
        if dht_device:
            try:
                dht_device.exit()
                logger.info("GPIO cleanup 완료")
            except:
                pass
    
    atexit.register(cleanup_gpio)
except Exception as e:
    logger.error(f"✗ 센서 초기화 실패: {e}")
    logger.warning("센서 없이 가상 데이터 모드로 실행됩니다.")
    dht_device = None

# MQTT 설정
# EdgeCore의 eventBus는 기본적으로 internalMqttMode(포트 1884) 또는 externalMqttMode를 사용
# edgecore.yaml의 modules.eventBus.mqttMode 설정 확인 필요
MQTT_BROKER = "localhost"
MQTT_PORT = 1884
DEVICE_NAMESPACE = "default"
DEVICE_NAME = "my-doorlock"

# KubeEdge MQTT 토픽 규격 (공식 문서 기준)
# 구독: Cloud→Edge (Desired 값 변경 수신)
#   $hw/events/device/{namespace}/{device_name}/twin/update/delta
# 발행: Edge→Cloud (Reported 값 전송)
#   $hw/events/device/{namespace}/{device_name}/twin/update
SUBSCRIBE_TOPIC = f"$hw/events/device/{DEVICE_NAMESPACE}/{DEVICE_NAME}/twin/update/delta"
PUBLISH_TOPIC = f"$hw/events/device/{DEVICE_NAMESPACE}/{DEVICE_NAME}/twin/update"

# 전역 변수 - 도어락 비밀번호
current_password = "0000"
mqtt_connected = False  # MQTT 연결 상태 플래그

# 비밀번호 저장 파일 - 호스트와 공유 가능
PASSWORD_FILE = "/tmp/doorlock_password.txt"

def load_password():
    """파일에서 비밀번호 로드"""
    global current_password
    
    try:
        with open(PASSWORD_FILE, 'r') as f:
            saved_password = f.read().strip()
            if saved_password:
                current_password = saved_password
                logger.info(f"📁 저장된 비밀번호 로드: {current_password}")
    except FileNotFoundError:
        logger.info(f"📁 비밀번호 파일 없음. 기본값 사용: {current_password}")
        save_password(current_password)
    except Exception as e:
        logger.error(f"비밀번호 로드 실패: {e}")

def save_password(password):
    """파일에 비밀번호 저장 (영구 보관)"""
    try:
        with open(PASSWORD_FILE, 'w') as f:
            f.write(password)
        logger.info(f"💾 비밀번호 저장 완료: {password}")
    except Exception as e:
        logger.error(f"비밀번호 저장 실패: {e}")

def setup_arduino_serial():
    """아두이노 Serial 포트 초기화"""
    global arduino_serial
    
    try:
        logger.info(f"🔌 아두이노 Serial 연결 시도: {ARDUINO_PORT} @ {ARDUINO_BAUD_RATE}bps")
        arduino_serial = serial.Serial(
            port=ARDUINO_PORT,
            baudrate=ARDUINO_BAUD_RATE,
            timeout=1
        )
        arduino_serial.flush()  # 버퍼 비우기
        time.sleep(2)  # 아두이노 재부팅 대기 (필수)
        logger.info("✓ 아두이노 Serial 연결 성공")
        return True
        
    except serial.SerialException as e:
        logger.error(f"✗ 아두이노 Serial 연결 실패: {e}")
        logger.warning(f"포트 {ARDUINO_PORT}를 찾을 수 없습니다. 연결을 확인하세요.")
        arduino_serial = None
        return False
    except Exception as e:
        logger.error(f"✗ 예상치 못한 오류: {e}")
        arduino_serial = None
        return False

def send_arduino_command(command):
    global arduino_serial
    
    if arduino_serial is None or not arduino_serial.is_open:
        logger.warning("⚠️ 아두이노 Serial 연결되지 않음")
        # 재연결 시도
        if not setup_arduino_serial():
            return False
    
    try:
        # 명령을 바이트로 변환하여 전송 (끝에 \n 포함)
        message = f"{command}\n".encode('utf-8')
        arduino_serial.write(message)
        arduino_serial.flush()
        logger.info(f"📤 아두이노로 명령 전송: {command}")
        return True
        
    except serial.SerialException as e:
        logger.error(f"✗ Serial 전송 실패: {e}")
        # 연결 끔김 - 다음 시도에 재연결
        if arduino_serial:
            arduino_serial.close()
        arduino_serial = None
        return False
    except Exception as e:
        logger.error(f"✗ 전송 오류: {e}")
        return False

def control_doorlock(action="lock"):
    try:
        if action == "unlock":
            logger.info("🔓 도어락 열림 - 아두이노에 'open' 명령 전송")
            success = send_arduino_command("open")
            if success:
                logger.info("✓ 도어락 열림 명령 전송 성공 (90도)")
            return success
            
        elif action == "lock":
            logger.info("🔒 도어락 잠금 - 아두이노에 'close' 명령 전송")
            success = send_arduino_command("close")
            if success:
                logger.info("✓ 도어락 잠금 명령 전송 성공 (0도)")
            return success
        
        return False
        
    except Exception as e:
        logger.error(f"도어락 제어 실패: {e}")
        return False

def verify_password(input_password):
    global current_password
    
    if input_password == current_password:
        logger.info(f"✅ 비밀번호 일치! 도어락 열림")
        print("\n✅ 비밀번호 일치! 도어락을 엽니다...")
        control_doorlock("unlock")
        print("🔓 도어락 열림 (3초 후 자동 잠김)")
        time.sleep(3)  # 3초 후 자동 잠금
        control_doorlock("lock")
        print("🔒 도어락 잠김 완료\n")
        return True
    else:
        logger.warning(f"❌ 비밀번호 불일치: {input_password} != {current_password}")
        print(f"\n❌ 비밀번호가 일치하지 않습니다! (입력: {input_password})\n")
        return False

def cli_password_input_loop():
    logger.info("🎹 CLI 비밀번호 입력 모드 시작")
    print("\n" + "="*60)
    print("🔐 도어락 CLI 제어 모드")
    print("="*60)
    print("📝 비밀번호를 입력하여 도어락을 제어하세요.")
    print("🚫 종료하려면 'exit' 또는 'quit'을 입력하세요.")
    print(f"🔑 현재 비밀번호: {'*' * len(current_password)} ({len(current_password)}자리)")
    print("="*60 + "\n")
    
    while True:
        try:
            # 비밀번호 입력 받기
            user_input = input("🔒 비밀번호를 입력하세요: ").strip()
            
            # 종료 명령 처리
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("\n👋 CLI 비밀번호 입력 모드 종료")
                logger.info("CLI 비밀번호 입력 쓰레드 종료")
                break
            
            # 빈 입력 무시
            if not user_input:
                continue
            
            # 특수 명령: 현재 비밀번호 표시
            if user_input == "show":
                print(f"\n🔑 현재 비밀번호: {current_password}\n")
                continue
            
            # 비밀번호 검증 및 도어락 제어
            logger.info(f"CLI로부터 비밀번호 입력: {user_input}")
            verify_password(user_input)
            
        except EOFError:
            # 파일 끝 (Ctrl+D)
            print("\n\n👋 CLI 모드 종료 (EOF)")
            break
        except KeyboardInterrupt:
            # Ctrl+C
            print("\n\n👋 CLI 모드 종료 (Interrupt)")
            break
        except Exception as e:
            logger.error(f"CLI 입력 처리 오류: {e}")
            print(f"\n⚠️ 오류 발생: {e}\n")


def on_connect(client, userdata, flags, rc):
    """MQTT 브로커 연결 성공 시 호출되는 콜백"""
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        logger.info(f"✓ MQTT 브로커 연결 성공: {MQTT_BROKER}:{MQTT_PORT}")
        # 델타 업데이트 토픽 구독
        client.subscribe(SUBSCRIBE_TOPIC)
        logger.info(f"✓ 구독 완료: {SUBSCRIBE_TOPIC}")
    else:
        mqtt_connected = False
        logger.error(f"✗ MQTT 브로커 연결 실패: Return Code {rc}")


def on_message(client, userdata, msg):
    """MQTT 메시지 수신 시 호출되는 콜백"""
    global current_password
    
    try:
        logger.info(f"📩 메시지 수신: Topic={msg.topic}")
        
        # JSON 페이로드 파싱
        payload = json.loads(msg.payload.decode('utf-8'))
        logger.info(f"📦 Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        # 형식 1: Dashboard에서 직접 보낸 Delta (최상위에 property)
        # {"password": {"expected": {...}, "actual": {...}}}
        if 'password' in payload and isinstance(payload['password'], dict):
            if 'expected' in payload['password']:
                expected = payload['password']['expected']
                new_password = expected.get('value', '')
                if new_password:
                    logger.info(f"🔑 비밀번호 변경 요청: {current_password} -> {new_password}")
                    current_password = new_password
                    save_password(current_password)  # 파일에 영구 저장
                    logger.info(f"✓ 비밀번호가 '{current_password}'로 업데이트되었습니다.")
                    
                    # 터미널에 명확한 알림 출력
                    kst_time = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                    print(f"\n{'='*70}")
                    print(f"🔔 [비밀번호 변경 알림] - {kst_time} KST")
                    print(f"{'='*70}")
                    print(f"  이전 비밀번호: {current_password if current_password == new_password else '****'}")
                    print(f"  새 비밀번호:   {new_password}")
                    print(f"  변경 경로:     Dashboard → MQTT → Mapper")
                    print(f"{'='*70}\n")
                    
                    # 변경 사항을 Cloud에 보고
                    report_password_change(client, current_password)
                    return
        
        # 형식 2: DeviceTwin에서 보낸 Delta (delta 필드 안에)
        # {"delta": {"password": "new_value"}}
        if 'delta' in payload:
            delta = payload['delta']
            if 'password' in delta:
                new_password = delta['password']
                logger.info(f"🔑 비밀번호 변경 요청: {current_password} -> {new_password}")
                old_password = current_password
                current_password = new_password
                save_password(current_password)  # 파일에 영구 저장
                logger.info(f"✓ 비밀번호가 '{current_password}'로 업데이트되었습니다.")
                
                # 터미널에 명확한 알림 출력
                kst_time = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n{'='*70}")
                print(f"🔔 [비밀번호 변경 알림] - {kst_time} KST")
                print(f"{'='*70}")
                print(f"  이전 비밀번호: {old_password}")
                print(f"  새 비밀번호:   {new_password}")
                print(f"  변경 경로:     Dashboard → MQTT → Mapper")
                print(f"{'='*70}\n")
                
                # 변경 사항을 Cloud에 보고
                report_password_change(client, current_password)
                return
        
        logger.warning(f"⚠️ 알 수 없는 메시지 형식: {payload}")
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON 파싱 오류: {e}")
    except Exception as e:
        logger.error(f"메시지 처리 중 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())


def on_publish(client, userdata, mid):
    """MQTT 메시지 발행 성공 시 호출되는 콜백"""
    logger.info(f"✓ MQTT 메시지 발행 완료 (Message ID: {mid})")


def on_disconnect(client, userdata, rc):
    """MQTT 브로커 연결 끊김 시 호출되는 콜백"""
    global mqtt_connected
    mqtt_connected = False
    if rc != 0:
        logger.warning(f"✗ 예상치 못한 연결 끊김. Return Code: {rc}")
        logger.info("재연결 시도 중...")
    else:
        logger.info("정상적으로 연결 종료")


def report_password_change(client, password):
    """비밀번호 변경 사항을 Cloud에 보고
    
    KubeEdge DeviceTwin 페이로드 형식 (types.go MsgTwin 구조체 기준):
    {
        "event_id": "...",
        "timestamp": ...,
        "twin": {
            "<property_name>": {
                "actual": {
                    "value": "<value>",
                    "metadata": {"timestamp": ...}
                },
                "metadata": {"type": "<type>"}
            }
        }
    }
    """
    if not mqtt_connected:
        logger.error("✗ MQTT 연결되지 않음 - 비밀번호 변경 보고 실패")
        return
        
    try:
        pwd_payload = {
            "event_id": f"pwd-update-{int(time.time())}",
            "timestamp": int(time.time() * 1000),
            "twin": {
                "password": {
                    "actual": {
                        "value": password,
                        "metadata": {
                            "timestamp": int(time.time() * 1000)
                        }
                    },
                    "metadata": {
                        "type": "string"
                    }
                }
            }
        }
        
        logger.info(f"→ 비밀번호 변경 보고 전송 중...")
        logger.debug(f"  Topic: {PUBLISH_TOPIC}")
        logger.debug(f"  Payload: {json.dumps(pwd_payload)}")
        
        msg_info = client.publish(PUBLISH_TOPIC, json.dumps(pwd_payload), qos=1)
        
        if msg_info.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"  발행 요청 성공 (MID: {msg_info.mid})")
            logger.info(f"✓ 비밀번호 변경 보고 요청 완료: {password}")
        else:
            logger.error(f"✗ 비밀번호 변경 보고 실패: Return Code {msg_info.rc}")
            
    except Exception as e:
        logger.error(f"✗ 비밀번호 변경 보고 중 오류: {e}")


def read_temperature_sensor():
    """온도 센서 값을 읽는 함수 (DHT11) - 타임아웃 포함"""
    if dht_device is None:
        logger.warning("센서가 초기화되지 않았습니다. 가상 값을 반환합니다.")
        return round(20.0 + random.random() * 10.0, 2)

    # 최대 2번 재시도 (시간 절약)
    for attempt in range(2):
        try:
            # 타임아웃을 사용한 센서 읽기
            result = {'temp': None, 'humidity': None, 'error': None, 'done': False}
            
            def read_sensor_thread():
                try:
                    result['temp'] = dht_device.temperature
                    result['humidity'] = dht_device.humidity
                    result['done'] = True
                except Exception as e:
                    result['error'] = e
                    result['done'] = True
            
            # 별도 스레드로 센서 읽기 (3초 타임아웃)
            thread = Thread(target=read_sensor_thread, daemon=True)
            thread.start()
            thread.join(timeout=3.0)
            
            if not result['done']:
                logger.warning(f"센서 읽기 타임아웃 (시도 {attempt + 1}/2)")
                continue
                
            if result['error']:
                if isinstance(result['error'], RuntimeError):
                    logger.warning(f"센서 읽기 오류 (시도 {attempt + 1}/2): {result['error'].args[0]}")
                else:
                    logger.error(f"센서 오류 (시도 {attempt + 1}/2): {result['error']}")
                continue
            
            temperature = result['temp']
            humidity = result['humidity']
            
            if temperature is not None and humidity is not None:
                logger.info(f"센서 읽기 성공 - 온도: {temperature:.1f}°C, 습도: {humidity}%")
                return temperature
            else:
                logger.warning(f"센서 값이 None입니다 (시도 {attempt + 1}/2)")
                
        except Exception as error:
            logger.error(f"예상치 못한 오류 (시도 {attempt + 1}/2): {error}")
        
        # 재시도 전 대기 (DHT11은 최소 2초 간격 필요)
        if attempt < 1:
            time.sleep(2.5)
    
    # 모두 실패
    logger.error("센서 값 읽기 실패")
    return None


def report_temperature(client):
    """온도 센서 데이터를 Cloud에 보고
    
    KubeEdge DeviceTwin 페이로드 형식 (types.go MsgTwin 구조체 기준):
    twin.<property>.actual.value에 값을 넣어야 status.twins[].reported.value로 반영됨
    """
    if not mqtt_connected:
        logger.error("✗ MQTT 연결되지 않음 - 온도 데이터 전송 실패")
        return
        
    try:
        logger.info("온도 센서 읽기 시도...")
        temperature = read_temperature_sensor()
        
        if temperature is None:
            logger.warning("온도 데이터 전송 생략 (센서 읽기 실패)")
            return # 읽기 실패 시 전송하지 않음

        temp_payload = {
            "event_id": f"temp-update-{int(time.time())}",
            "timestamp": int(time.time() * 1000),
            "twin": {
                "temperature": {
                    "actual": {
                        "value": str(temperature),
                        "metadata": {
                            "timestamp": int(time.time() * 1000)
                        }
                    },
                    "metadata": {
                        "type": "float"
                    }
                }
            }
        }
        
        logger.info(f"→ 온도 데이터 전송 중: {temperature}°C")
        logger.debug(f"  Topic: {PUBLISH_TOPIC}")
        logger.debug(f"  Payload: {json.dumps(temp_payload)}")
        
        msg_info = client.publish(PUBLISH_TOPIC, json.dumps(temp_payload), qos=1)
        
        if msg_info.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"  발행 요청 성공 (MID: {msg_info.mid})")
            logger.info(f"✓ 온도 데이터 전송 요청 완료: {temperature}°C")
        else:
            logger.error(f"✗ 온도 데이터 전송 실패: Return Code {msg_info.rc}")
            
    except Exception as e:
        logger.error(f"✗ 온도 데이터 전송 중 오류: {e}")


def main():
    """메인 함수"""
    logger.info("=== KubeEdge Doorlock Mapper 시작 ===")
    
    # 저장된 비밀번호 로드
    load_password()
    
    # 아두이노 Serial 연결 초기화
    setup_arduino_serial()
    
    logger.info(f"초기 비밀번호: {current_password}")
    logger.info(f"MQTT 브로커: {MQTT_BROKER}:{MQTT_PORT}")
    logger.info(f"구독 토픽: {SUBSCRIBE_TOPIC}")
    logger.info(f"발행 토픽: {PUBLISH_TOPIC}")
    
    # MQTT 클라이언트 생성
    client = mqtt.Client(client_id=f"doorlock-mapper-{int(time.time())}")
    
    # 콜백 함수 등록
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_publish = on_publish  # 발행 완료 콜백 추가
    client.on_disconnect = on_disconnect
    
    try:
        # MQTT 브로커 연결
        logger.info(f"MQTT 브로커 연결 시도: {MQTT_BROKER}:{MQTT_PORT}")
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        
        # 백그라운드 네트워크 루프 시작
        client.loop_start()
        
        # 연결 대기 (최대 5초)
        logger.info("MQTT 연결 대기 중...")
        for i in range(10):
            if mqtt_connected:
                logger.info("✓ MQTT 연결 완료")
                break
            time.sleep(0.5)
        else:
            logger.error("✗ MQTT 연결 타임아웃")
        
        # CLI 비밀번호 입력 쓰레드 시작 (별도 쓰레드로 실행)
        cli_thread = Thread(target=cli_password_input_loop, daemon=True, name="CLI-Input")
        cli_thread.start()
        logger.info("✓ CLI 비밀번호 입력 쓰레드 시작")
        
        # 초기 비밀번호 상태 보고
        logger.info("초기 비밀번호 상태 보고 중...")
        time.sleep(1)  # MQTT 연결 안정화 대기
        report_password_change(client, current_password)
        
        # 주기적으로 온도 데이터 전송
        logger.info("온도 센서 모니터링 시작 (5초 간격)")
        while True:
            time.sleep(5)  # 5초마다 온도 전송
            report_temperature(client)
            logger.debug(f"현재 비밀번호: {current_password}")
            logger.debug(f"MQTT 연결 상태: {mqtt_connected}")
            
    except KeyboardInterrupt:
        logger.info("사용자에 의해 종료됨")
    except Exception as e:
        logger.error(f"애플리케이션 오류: {e}")
    finally:
        logger.info("MQTT 연결 종료 중...")
        client.loop_stop()
        client.disconnect()
        
        # 아두이노 Serial 연결 종료
        if arduino_serial and arduino_serial.is_open:
            logger.info("아두이노 Serial 연결 종료 중...")
            arduino_serial.close()
            logger.info("✓ Serial 연결 종료 완료")
        
        # DHT 센서 정리
        if dht_device:
            dht_device.exit()
            
        logger.info("=== Doorlock Mapper 종료 ===")


if __name__ == "__main__":
    main()
