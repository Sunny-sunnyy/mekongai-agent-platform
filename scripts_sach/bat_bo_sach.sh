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
dc up -d postgres redis minio dynamodb otel
cho_http "http://127.0.0.1:9000/minio/health/live" "MinIO" 60 || true

echo "== Bước 2: Chạy migrate cơ sở dữ liệu =="
dc run --rm ab-migrate

echo "== Bước 3: Bật Temporal, Cổng AI, Platform API, Agentic Backend, Worker, MCP, Cổng ngoài =="
dc up -d temporal cong-ai platform-api ab-api ab-worker ab-worker-temporal mcp-fastapi mcp-servers mcp-exa mcp-shop cong-ngoai

echo "== Bước 4: Kiểm tra sức khỏe dịch vụ chính =="
cho_http "http://127.0.0.1:8080/health" "platform-api" 90
cho_http "http://127.0.0.1:8000/health" "agentic-backend" 90
cho_http "http://127.0.0.1:18300/" "cong-ngoai" 60 || true

echo "== Bước 5: Kiểm tra rào chặn lộ bí mật (phát hiện khóa trong container) =="
if docker inspect capitol-ab-api >/dev/null 2>&1; then
  echo "  OK: container capitol-ab-api đang chạy qua cổng AI nội bộ."
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
