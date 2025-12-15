import time
import json
import board
import adafruit_dht
import paho.mqtt.client as mqtt

# ---------------- 설정 부분 ----------------
# 1. KubeEdge에 등록할 디바이스 이름 (Cloud의 Device yaml 이름과 일치해야 함)
DEVICE_NAME = "sensor-device-01"

# 2. 센서 설정 (GPIO 23번)
dht_device = adafruit_dht.DHT11(board.D23)

# 3. MQTT 설정 (Edge 노드 내부 통신)
BROKER_ADDRESS = "127.0.0.1"
BROKER_PORT = 1883
TOPIC = f"$hw/events/upload/devices/{DEVICE_NAME}/twin/update"
# ------------------------------------------

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"MQTT 브로커 연결 성공! (Topic: {TOPIC})")
    else:
        print(f"연결 실패, 코드: {rc}")

# MQTT 클라이언트 초기화
client = mqtt.Client()
client.on_connect = on_connect

try:
    client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
    client.loop_start() # 백그라운드에서 통신 시작
except Exception as e:
    print(f"MQTT 연결 오류: {e}")

print(f"[{DEVICE_NAME}] 온습도 데이터 전송을 시작합니다...")

try:
    while True:
        try:
            # 센서 값 읽기
            temperature = dht_device.temperature
            humidity = dht_device.humidity

            if humidity is not None and temperature is not None:
                # ------------------------------------------------------
                # [핵심] KubeEdge가 이해할 수 있는 JSON 데이터 만들기
                # ------------------------------------------------------
                payload = {
                    "event_id": str(int(time.time())),
                    "timestamp": int(time.time() * 1000),
                    "twin": {
                        "temperature": {
                            "actual": { "value": str(temperature) },
                            "metadata": { "type": "Updated" }
                        },
                        "humidity": {
                            "actual": { "value": str(humidity) },
                            "metadata": { "type": "Updated" }
                        }
                    }
                }
                
                # 데이터 전송 (Publish)
                client.publish(TOPIC, json.dumps(payload))
                print(f"전송 완료 -> 온도: {temperature}C, 습도: {humidity}%")
            
            else:
                print("센서 값 읽기 실패 (값 없음)")

        except RuntimeError as error:
            # DHT 센서는 종종 읽기 에러가 발생하므로 무시하고 넘어갑니다.
            print(f"센서 읽기 오류 (재시도 중): {error.args[0]}")
            time.sleep(2.0)
            continue
        except Exception as error:
            dht_device.exit()
            raise error

        time.sleep(3.0) # 3초 간격으로 전송

except KeyboardInterrupt:
    print("프로그램을 종료합니다.")
finally:
    dht_device.exit()
    client.loop_stop()