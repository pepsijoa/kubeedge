#!/bin/bash
set -e

################################################################################
# KubeEdge 스마트 도어락 프로젝트 - 전체 설정 및 배포 스크립트
# 
# 이 스크립트는 KubeEdge 프로젝트를 처음부터 끝까지 설정하고 배포합니다.
# 
# 사전 요구사항:
#   - Cloud 노드: Kubernetes (K3s) 설치, CloudCore 설치
#   - Edge 노드: EdgeCore 설치 및 Cloud 노드에 조인 완료
#   - Docker, kubectl 명령어 사용 가능
#   - Docker Hub 계정 로그인 (이미지 push용)
# 
# 사용법:
#   ./start_project.sh [옵션]
# 
# 옵션:
#   --skip-check      시스템 체크 건너뛰기
#   --skip-cloudcore  CloudCore 시작 건너뛰기
#   --skip-build      Docker 이미지 빌드 건너뛰기
#   --skip-push       Docker Hub Push 건너뛰기
#   --quick           모든 체크/빌드/푸시 건너뛰고 배포만 수행
################################################################################

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 설정 변수
CLOUD_NODE_IP="192.168.0.5"
EDGE_NODE_IP="192.168.0.12"
EDGE_NODE_NAME="raspberrypi-temp"
EDGE_NODE_USER="kkw"
DASHBOARD_VERSION="v9"
MAPPER_VERSION="v3"
DOCKER_USERNAME="kkw4278"

# 옵션 파싱
SKIP_CHECK=false
SKIP_CLOUDCORE=false
SKIP_BUILD=false
SKIP_PUSH=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-check)
            SKIP_CHECK=true
            shift
            ;;
        --skip-cloudcore)
            SKIP_CLOUDCORE=true
            shift
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --skip-push)
            SKIP_PUSH=true
            shift
            ;;
        --quick)
            SKIP_CHECK=true
            SKIP_CLOUDCORE=true
            SKIP_BUILD=true
            SKIP_PUSH=true
            shift
            ;;
        *)
            echo "알 수 없는 옵션: $1"
            exit 1
            ;;
    esac
done

# 스크립트 위치로 이동
cd "$(dirname "$0")"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  KubeEdge 스마트 도어락 프로젝트 - 전체 설정 및 배포         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

################################################################################
# 1단계: 시스템 요구사항 체크
################################################################################
if [ "$SKIP_CHECK" = false ]; then
    echo -e "${YELLOW}[1/9] 시스템 요구사항 체크 중...${NC}"
    
    # kubectl 설치 확인
    if ! command -v kubectl &> /dev/null; then
        echo -e "${RED}✗ kubectl이 설치되어 있지 않습니다.${NC}"
        echo "  설치: curl -LO https://dl.k8s.io/release/\$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/arm64/kubectl"
        exit 1
    fi
    echo -e "${GREEN}✓ kubectl 설치 확인${NC}"
    
    # Docker 설치 확인
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}✗ Docker가 설치되어 있지 않습니다.${NC}"
        echo "  설치: curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh"
        exit 1
    fi
    echo -e "${GREEN}✓ Docker 설치 확인${NC}"
    
    # Kubernetes 클러스터 접근 확인
    if ! kubectl cluster-info &> /dev/null; then
        echo -e "${RED}✗ Kubernetes 클러스터에 접근할 수 없습니다.${NC}"
        echo "  kubectl 설정을 확인하세요: kubectl get nodes"
        exit 1
    fi
    echo -e "${GREEN}✓ Kubernetes 클러스터 접근 가능${NC}"
    
    # Edge 노드 상태 확인
    if kubectl get node "$EDGE_NODE_NAME" &> /dev/null; then
        NODE_STATUS=$(kubectl get node "$EDGE_NODE_NAME" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
        if [ "$NODE_STATUS" = "True" ]; then
            echo -e "${GREEN}✓ Edge 노드 '$EDGE_NODE_NAME' 상태: Ready${NC}"
        else
            echo -e "${YELLOW}⚠ Edge 노드 '$EDGE_NODE_NAME' 상태: NotReady${NC}"
            echo "  EdgeCore가 실행 중인지 확인하세요: ssh $EDGE_NODE_USER@$EDGE_NODE_IP 'sudo systemctl status edgecore'"
        fi
    else
        echo -e "${YELLOW}⚠ Edge 노드 '$EDGE_NODE_NAME'를 찾을 수 없습니다.${NC}"
        echo "  EdgeCore를 Cloud에 조인했는지 확인하세요."
    fi
    
    # CloudCore 설치 확인
    if ! command -v cloudcore &> /dev/null; then
        echo -e "${YELLOW}⚠ CloudCore가 설치되어 있지 않습니다.${NC}"
        echo "  설치: https://kubeedge.io/docs/setup/install-with-binary/"
    else
        echo -e "${GREEN}✓ CloudCore 설치 확인${NC}"
    fi
    
    # SSH 접근 확인 (Edge 노드)
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$EDGE_NODE_USER@$EDGE_NODE_IP" "echo" &> /dev/null; then
        echo -e "${GREEN}✓ Edge 노드 SSH 접근 가능 ($EDGE_NODE_USER@$EDGE_NODE_IP)${NC}"
    else
        echo -e "${YELLOW}⚠ Edge 노드 SSH 접근 불가${NC}"
        echo "  Dashboard의 MQTT Delta 발행 기능이 제한됩니다."
    fi
    
    echo -e "${GREEN}시스템 체크 완료!${NC}\n"
else
    echo -e "${YELLOW}[1/9] 시스템 체크 건너뜀 (--skip-check)${NC}\n"
fi

################################################################################
# 2단계: CloudCore 시작
################################################################################
if [ "$SKIP_CLOUDCORE" = false ]; then
    echo -e "${YELLOW}[2/9] CloudCore 상태 확인 및 시작...${NC}"
    
    if pgrep -x "cloudcore" > /dev/null; then
        echo -e "${GREEN}✓ CloudCore가 이미 실행 중입니다.${NC}"
    else
        echo "CloudCore를 백그라운드로 시작합니다..."
        nohup sudo cloudcore > ./cloudcore.log 2>&1 &
        sleep 3
        
        if pgrep -x "cloudcore" > /dev/null; then
            echo -e "${GREEN}✓ CloudCore 시작 완료${NC}"
            echo "  로그: tail -f $(pwd)/cloudcore.log"
        else
            echo -e "${RED}✗ CloudCore 시작 실패${NC}"
            echo "  로그를 확인하세요: cat $(pwd)/cloudcore.log"
            exit 1
        fi
    fi
    echo ""
else
    echo -e "${YELLOW}[2/9] CloudCore 시작 건너뜀 (--skip-cloudcore)${NC}\n"
fi

################################################################################
# 3단계: Edge 노드에서 EdgeCore 상태 확인
################################################################################
echo -e "${YELLOW}[3/9] Edge 노드 EdgeCore 상태 확인...${NC}"

if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$EDGE_NODE_USER@$EDGE_NODE_IP" "sudo systemctl is-active edgecore" &> /dev/null; then
    echo -e "${GREEN}✓ EdgeCore가 Edge 노드에서 실행 중입니다.${NC}"
else
    echo -e "${YELLOW}⚠ EdgeCore가 실행 중이 아닙니다.${NC}"
    echo "  Edge 노드에서 EdgeCore 시작:"
    echo "    ssh $EDGE_NODE_USER@$EDGE_NODE_IP 'sudo systemctl start edgecore'"
    echo "    ssh $EDGE_NODE_USER@$EDGE_NODE_IP 'sudo systemctl status edgecore'"
fi
echo ""

################################################################################
# 4단계: 기존 리소스 정리
################################################################################
echo -e "${YELLOW}[4/9] 기존 Kubernetes 리소스 정리 중...${NC}"

kubectl delete -f deploy/mapper-deploy.yaml --ignore-not-found
kubectl delete -f deploy/dashboard-deploy.yaml --ignore-not-found
kubectl delete -f deploy/device-instance.yaml --ignore-not-found
kubectl delete -f deploy/device-model.yaml --ignore-not-found
kubectl delete -f deploy/dashboard-rbac.yaml --ignore-not-found

echo -e "${GREEN}✓ 기존 리소스 정리 완료${NC}\n"

################################################################################
# 5단계: Docker 이미지 빌드
################################################################################
if [ "$SKIP_BUILD" = false ]; then
    echo -e "${YELLOW}[5/9] Docker 이미지 빌드 중...${NC}"
    
    # Dashboard 이미지 빌드
    echo "  → Dashboard 이미지 빌드 중... ($DOCKER_USERNAME/kubeedge-dashboard:$DASHBOARD_VERSION)"
    if sudo docker build -t "$DOCKER_USERNAME/kubeedge-dashboard:$DASHBOARD_VERSION" ./dashboard; then
        echo -e "${GREEN}  ✓ Dashboard 이미지 빌드 성공${NC}"
    else
        echo -e "${RED}  ✗ Dashboard 이미지 빌드 실패${NC}"
        exit 1
    fi
    
    # Mapper 이미지 빌드
    echo "  → Mapper 이미지 빌드 중... ($DOCKER_USERNAME/kubeedge-mapper:$MAPPER_VERSION)"
    if sudo docker build -t "$DOCKER_USERNAME/kubeedge-mapper:$MAPPER_VERSION" ./mapper; then
        echo -e "${GREEN}  ✓ Mapper 이미지 빌드 성공${NC}"
    else
        echo -e "${RED}  ✗ Mapper 이미지 빌드 실패${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}Docker 이미지 빌드 완료!${NC}\n"
else
    echo -e "${YELLOW}[5/9] Docker 이미지 빌드 건너뜀 (--skip-build)${NC}\n"
fi

################################################################################
# 6단계: Docker Hub에 이미지 Push
################################################################################
if [ "$SKIP_PUSH" = false ]; then
    echo -e "${YELLOW}[6/9] Docker Hub에 이미지 Push 중...${NC}"
    
    # Docker Hub 로그인 확인
    if ! sudo docker info | grep -q "Username"; then
        echo -e "${YELLOW}⚠ Docker Hub에 로그인되어 있지 않습니다.${NC}"
        echo "  로그인: sudo docker login"
        read -p "계속하시겠습니까? (y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
    
    # Dashboard 이미지 Push
    echo "  → Dashboard 이미지 Push 중..."
    if sudo docker push "$DOCKER_USERNAME/kubeedge-dashboard:$DASHBOARD_VERSION"; then
        echo -e "${GREEN}  ✓ Dashboard 이미지 Push 성공${NC}"
    else
        echo -e "${RED}  ✗ Dashboard 이미지 Push 실패${NC}"
        exit 1
    fi
    
    # Mapper 이미지 Push
    echo "  → Mapper 이미지 Push 중..."
    if sudo docker push "$DOCKER_USERNAME/kubeedge-mapper:$MAPPER_VERSION"; then
        echo -e "${GREEN}  ✓ Mapper 이미지 Push 성공${NC}"
    else
        echo -e "${RED}  ✗ Mapper 이미지 Push 실패${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}Docker Hub Push 완료!${NC}\n"
else
    echo -e "${YELLOW}[6/9] Docker Hub Push 건너뜀 (--skip-push)${NC}\n"
fi

################################################################################
# 7단계: KubeEdge CRD 및 RBAC 배포
################################################################################
echo -e "${YELLOW}[7/9] KubeEdge Device CRD 및 RBAC 배포 중...${NC}"

# DeviceModel 배포
echo "  → DeviceModel 배포 중..."
if kubectl apply -f deploy/device-model.yaml; then
    echo -e "${GREEN}  ✓ DeviceModel 배포 성공${NC}"
else
    echo -e "${RED}  ✗ DeviceModel 배포 실패${NC}"
    exit 1
fi

# Dashboard RBAC 배포
echo "  → Dashboard RBAC 배포 중..."
if kubectl apply -f deploy/dashboard-rbac.yaml; then
    echo -e "${GREEN}  ✓ Dashboard RBAC 배포 성공${NC}"
else
    echo -e "${RED}  ✗ Dashboard RBAC 배포 실패${NC}"
    exit 1
fi

echo -e "${GREEN}CRD 및 RBAC 배포 완료!${NC}\n"

################################################################################
# 8단계: 애플리케이션 배포
################################################################################
echo -e "${YELLOW}[8/9] 애플리케이션 배포 중...${NC}"

# Device Instance 생성
echo "  → Device Instance 생성 중..."
if kubectl apply -f deploy/device-instance.yaml; then
    echo -e "${GREEN}  ✓ Device Instance 'my-doorlock' 생성 완료${NC}"
else
    echo -e "${RED}  ✗ Device Instance 생성 실패${NC}"
    exit 1
fi

# CRD 등록 대기
echo "  → CRD 등록 대기 중 (2초)..."
sleep 2

# Dashboard 배포
echo "  → Dashboard 배포 중..."
if kubectl apply -f deploy/dashboard-deploy.yaml; then
    echo -e "${GREEN}  ✓ Dashboard 배포 성공${NC}"
else
    echo -e "${RED}  ✗ Dashboard 배포 실패${NC}"
    exit 1
fi

# Mapper 배포
echo "  → Mapper 배포 중..."
if kubectl apply -f deploy/mapper-deploy.yaml; then
    echo -e "${GREEN}  ✓ Mapper 배포 성공${NC}"
else
    echo -e "${RED}  ✗ Mapper 배포 실패${NC}"
    exit 1
fi

echo -e "${GREEN}애플리케이션 배포 완료!${NC}\n"

################################################################################
# 9단계: 배포 상태 확인
################################################################################
echo -e "${YELLOW}[9/9] 배포 상태 확인 중...${NC}"

echo "  → Pod 상태 확인 (10초 대기)..."
sleep 10

echo ""
echo -e "${BLUE}════════════ Pod 상태 ════════════${NC}"
kubectl get pods -o wide | grep -E "NAME|dashboard|mapper"

echo ""
echo -e "${BLUE}════════════ Device 상태 ════════════${NC}"
kubectl get devices

echo ""
echo -e "${BLUE}════════════ Node 상태 ════════════${NC}"
kubectl get nodes

echo ""

################################################################################
# 배포 완료 및 접속 정보
################################################################################
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              배포가 성공적으로 완료되었습니다!                 ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

CLOUD_IP=$(hostname -I | awk '{print $1}')
echo -e "${BLUE}📊 Dashboard 접속:${NC}"
echo "   http://$CLOUD_IP:30000"
echo ""

echo -e "${BLUE}🔍 상태 확인 명령어:${NC}"
echo "   # Pod 로그 확인"
echo "   kubectl logs -f deployment/dashboard"
echo "   kubectl logs -f deployment/doorlock-mapper"
echo ""
echo "   # Device Twin 실시간 모니터링"
echo "   watch -n 1 \"kubectl get device my-doorlock -o jsonpath='{.status.twins}' | python3 -m json.tool\""
echo ""
echo "   # Edge 노드 비밀번호 파일 확인"
echo "   ssh $EDGE_NODE_USER@$EDGE_NODE_IP 'cat /tmp/doorlock_password.txt'"
echo ""

echo -e "${BLUE}🔧 비밀번호 변경 테스트:${NC}"
echo "   curl -X POST http://$CLOUD_IP:30000/api/device/my-doorlock/update/password/1234"
echo ""

echo -e "${BLUE}📚 추가 문서:${NC}"
echo "   - 프로젝트 요약: cat PROJECT_SUMMARY.md"
echo "   - 상세 스펙: cat SPEC.md"
echo "   - CloudCore 로그: tail -f cloudcore.log"
echo ""

echo -e "${YELLOW}💡 Tip:${NC} 빠른 재배포를 원한다면 '--quick' 옵션을 사용하세요."
echo "   ./start_project.sh --quick"
echo ""
