#!/usr/bin/env bash
# Khởi động tuần tự toàn bộ nền tảng trên máy mới với các bước kiểm tra từng nấc.
# Mặc định bật lõi + cổng AI + cổng ngoài + worker; tùy chọn bật KPI và cổng RAG.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_chung.sach.sh
source "${SCRIPT_DIR}/_chung.sach.sh"

BAT_KPI="${BAT_KPI:-co}"
BAT_RAG="${BAT_RAG:-khong}"

echo "== Bước 0: Kiểm tra điều kiện máy chủ =="
command -v docker >/dev/null 2>&1 || { echo "LỖI: chưa cài docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "LỖI: Docker daemon chưa chạy" >&2; exit 1; }
can_ram 4000

for f in "agentic-backend.env" "agentic-backend.khoa.env" "platform-api.env" "cong_ai.khoa.env"; do
  if [[ ! -f "${ROOT_DIR}/env/${f}" ]]; then
    echo "LỖI: thiếu ${ROOT_DIR}/env/${f}. Hãy chạy khoi_tao_lan_dau.sh trước." >&2
    exit 1
  fi
done

echo "== Bước 1: Bật dịch vụ nền tảng (DB, Redis, MinIO, DynamoDB, OTEL) =="
dc run --rm --no-deps --user 0:0 --entrypoint sh dynamodb -c 'chown -R 1000:1000 /home/dynamodblocal/data' >/dev/null 2>&1 || true
dc up -d postgres redis minio dynamodb otel
if docker inspect -f '{{.State.Running}}' capitol-minio 2>/dev/null | grep -qx true; then
  echo "OK: MinIO đang chạy trên mạng nội bộ s3_noi_bo"
fi

echo "== Bước 2: Chạy migrate cơ sở dữ liệu =="
dc run --rm ab-migrate

echo "== Bước 3: Bật Temporal, Cổng AI, Platform API, Agentic Backend, Worker, MCP, Cổng ngoài =="
dc up -d temporal cong-ai platform-api ab-api ab-worker ab-worker-temporal mcp-fastapi mcp-servers mcp-exa mcp-shop cong-ngoai

echo "== Bước 4: Kiểm tra sức khỏe dịch vụ chính =="
cho_health_container() {
  local container="$1"
  local url="$2"
  local toi_da="${3:-90}"
  local i=0
  while (( i < toi_da )); do
    if docker exec "${container}" python -I -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('${url}', timeout=3).status == 200 else 1)" >/dev/null 2>&1; then
      echo "OK: ${container} sẵn sàng (${url})"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "LỖI: hết thời gian chờ ${container} tại ${url}" >&2
  return 1
}
cho_health_container "capitol-platform-api" "http://127.0.0.1:80/health" 90
cho_health_container "capitol-ab-api" "http://127.0.0.1:80/health" 90
if kiem_cong_ban 18300; then
  echo "OK: cong-ngoai đang lắng nghe cổng 18300"
fi

echo "== Bước 5: Kiểm tra rào chặn lộ bí mật (phát hiện khóa trong container) =="
if docker inspect capitol-ab-api >/dev/null 2>&1; then
  echo "  OK: container capitol-ab-api đang chạy qua cổng AI nội bộ."
fi

mkdir -p "${CAPITOL_RUNTIME_DIR}"
if [[ ! -s "${CAPITOL_RUNTIME_DIR}/org_id.txt" ]]; then
  echo "== Bước 5b: Khởi tạo tổ chức mặc định & danh mục khối (lần đầu) =="
  ORG_ID="$(docker exec -w /srv/src capitol-platform-api python cli.py create-org "MekongAI chay thu" | python3 -I -c 'import re,sys; m=re.search(r"\"orgid\":\s*\"([^\"]+)\"", sys.stdin.read()); print(m.group(1) if m else "")')"
  if [[ -z "${ORG_ID}" ]]; then
    echo "LỖI: không lấy được orgid khi tạo tổ chức mặc định." >&2
    exit 1
  fi
  printf '%s\n' "${ORG_ID}" > "${CAPITOL_RUNTIME_DIR}/org_id.txt"
  docker exec -w /srv/src capitol-platform-api python cli.py seed-org-node-access-config "${ORG_ID}" >/dev/null
  echo "  OK: đã khởi tạo tổ chức (${ORG_ID}) -> ${CAPITOL_RUNTIME_DIR}/org_id.txt"
fi

if [[ "${BAT_KPI}" == "co" && -d "${ROOT_DIR}/kpi_agent" ]]; then
  echo "== Bước 6: Bật cụm dịch vụ KPI (18401 / 18402 / 18403) =="
  bash "${SCRIPT_DIR}/bat_kpi_backend_sach.sh"
fi

if [[ "${BAT_RAG}" == "co" ]]; then
  echo "== Bước 7: Bật cổng trung chuyển RAG (19453) =="
  bash "${SCRIPT_DIR}/bat_cong_rag_sach.sh"
fi

echo
echo "Hoàn tất khởi động nền tảng. Truy cập giao diện điều hành theo hướng dẫn khởi chạy UI (cổng 18400)."
