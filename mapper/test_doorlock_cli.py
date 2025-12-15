#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
도어락 CLI 테스트 프로그램
Edge 라즈베리파이에서 직접 실행하여 비밀번호 입력 테스트
"""

import serial
import time
import os

# 설정
ARDUINO_PORT = os.getenv('ARDUINO_PORT', '/dev/ttyACM0')
ARDUINO_BAUD_RATE = int(os.getenv('ARDUINO_BAUD_RATE', '9600'))
PASSWORD_FILE = "/var/lib/doorlock/password.txt"  # mapper.py와 동일한 경로 사용

# 전역 변수
current_password = "0000"
arduino_serial = None

def load_password():
    """저장된 비밀번호 로드"""
    global current_password
    try:
        with open(PASSWORD_FILE, 'r') as f:
            saved_password = f.read().strip()
            if saved_password:
                current_password = saved_password
                print(f"📁 저장된 비밀번호 로드: {current_password}")
    except FileNotFoundError:
        print(f"📁 비밀번호 파일 없음. 기본값 사용: {current_password}")
    except Exception as e:
        print(f"⚠️ 비밀번호 로드 실패: {e}")

def setup_arduino_serial():
    """아두이노 Serial 포트 초기화"""
    global arduino_serial
    
    try:
        print(f"🔌 아두이노 Serial 연결 시도: {ARDUINO_PORT} @ {ARDUINO_BAUD_RATE}bps")
        arduino_serial = serial.Serial(
            port=ARDUINO_PORT,
            baudrate=ARDUINO_BAUD_RATE,
            timeout=1
        )
        arduino_serial.flush()
        time.sleep(2)  # 아두이노 재부팅 대기
        print("✓ 아두이노 Serial 연결 성공")
        return True
        
    except serial.SerialException as e:
        print(f"✗ 아두이노 Serial 연결 실패: {e}")
        print(f"⚠️ 포트 {ARDUINO_PORT}를 찾을 수 없습니다.")
        print("   도어락 제어는 시뮬레이션 모드로 동작합니다.")
        arduino_serial = None
        return False
    except Exception as e:
        print(f"✗ 예상치 못한 오류: {e}")
        arduino_serial = None
        return False

def send_arduino_command(command):
    """아두이노에 명령 전송"""
    global arduino_serial
    
    if arduino_serial is None or not arduino_serial.is_open:
        print(f"⚠️ Serial 미연결 - 시뮬레이션: '{command}' 명령")
        return False
    
    try:
        message = f"{command}\n".encode('utf-8')
        arduino_serial.write(message)
        arduino_serial.flush()
        print(f"📤 아두이노로 명령 전송: {command}")
        return True
        
    except Exception as e:
        print(f"✗ Serial 전송 실패: {e}")
        return False

def control_doorlock(action="lock"):
    """도어락 제어"""
    if action == "unlock":
        print("🔓 도어락 열림 - 아두이노에 'open' 명령 전송")
        send_arduino_command("open")
        print("✓ 도어락 열림 (90도)")
            
    elif action == "lock":
        print("🔒 도어락 잠금 - 아두이노에 'close' 명령 전송")
        send_arduino_command("close")
        print("✓ 도어락 잠금 (0도)")

def verify_password(input_password):
    """비밀번호 확인 및 도어락 제어"""
    global current_password
    
    if input_password == current_password:
        print(f"\n✅ 비밀번호 일치! 도어락을 엽니다...")
        control_doorlock("unlock")
        print("🕐 3초 후 자동 잠금...")
        
        for i in range(3, 0, -1):
            print(f"   {i}초...")
            time.sleep(1)
        
        control_doorlock("lock")
        print("✓ 도어락 잠금 완료\n")
        return True
    else:
        print(f"\n❌ 비밀번호가 일치하지 않습니다!")
        print(f"   입력: {input_password}")
        print(f"   정답: {'*' * len(current_password)}\n")
        return False

def main():
    """메인 함수"""
    print("\n" + "="*60)
    print("🔐 도어락 CLI 테스트 프로그램")
    print("="*60)
    
    # 비밀번호 로드
    load_password()
    
    # 아두이노 연결 시도
    setup_arduino_serial()
    
    print("\n" + "="*60)
    print("📝 비밀번호를 입력하여 도어락을 제어하세요.")
    print("🚫 종료하려면 'exit' 또는 'quit'을 입력하세요.")
    print(f"🔑 현재 비밀번호: {'*' * len(current_password)} ({len(current_password)}자리)")
    print("="*60 + "\n")
    
    try:
        while True:
            # 비밀번호 입력
            user_input = input("🔒 비밀번호를 입력하세요: ").strip()
            
            # 종료 명령
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("\n👋 프로그램을 종료합니다.")
                break
            
            # 빈 입력 무시
            if not user_input:
                continue
            
            # 현재 비밀번호 표시
            if user_input == "show":
                print(f"\n🔑 현재 비밀번호: {current_password}\n")
                continue
            
            # 비밀번호 검증
            verify_password(user_input)
            
    except KeyboardInterrupt:
        print("\n\n👋 프로그램 종료 (Ctrl+C)")
    except EOFError:
        print("\n\n👋 프로그램 종료 (EOF)")
    finally:
        # Serial 연결 종료
        if arduino_serial and arduino_serial.is_open:
            print("\n🔌 아두이노 Serial 연결 종료...")
            arduino_serial.close()
            print("✓ 종료 완료")
        print("\n" + "="*60)
        print("프로그램 종료")
        print("="*60 + "\n")

if __name__ == "__main__":
    main()
