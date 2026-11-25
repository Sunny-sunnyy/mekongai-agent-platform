#!/usr/bin/env bash
# Bật dịch vụ kpi-backend (bản đóng băng) + cổng tải lên (18402) + cổng xem kết quả (18403).
# Yêu cầu: đã chạy khoi_tao_lan_dau.sh và có thư mục kpi_agent/ + sample_data/thang_10/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_chung.sach.sh
source "${SCRIPT_DIR}/_chung.sach.sh"

KPI_BACKEND_YAML="${SCRIPT_DIR}/compose.kpi_backend.sach.yaml"
if [[ ! -f "${KPI_BACKEND_YAML}" ]]; then
  echo "LỖI: không thấy ${KPI_BACKEND_YAML}" >&2
  exit 1
fi

if [[ ! -d "${ROOT_DIR}/kpi_agent" || ! -d "${ROOT_DIR}/sample_data/thang_10" ]]; then
  echo "LỖI: thiếu thư mục kpi_agent/ hoặc sample_data/thang_10/ trong ${ROOT_DIR}" >&2
  exit 1
fi

mkdir -p "${CAPITOL_RUNTIME_DIR}/kpi_store/uploads" "${CAPITOL_RUNTIME_DIR}/kpi_van_tay"

echo "== 1. Bật kpi-backend (18401) =="
docker compose --project-directory "${ROOT_DIR}" -f "${KPI_BACKEND_YAML}" up -d kpi-backend
cho_http "http://127.0.0.1:18401/api/health" "kpi-backend" 60

echo "== 2. Kiểm tra danh sách công cụ và thông tin tĩnh của kpi-backend =="
curl -fsS --max-time 5 "http://127.0.0.1:18401/api/tools" | head -c 300
echo
curl -fsS --max-time 5 "http://127.0.0.1:18401/api/meta" | tee "${CAPITOL_RUNTIME_DIR}/kpi_van_tay/meta_backend.json"
echo

echo "== 3. Bật kpi-tai-len (18402) và kpi-xem-cong (18403) =="
dc -f "${ROOT_DIR}/compose.kpi_tai_len.yaml" -f "${ROOT_DIR}/compose.kpi_xem.yaml" up -d kpi-tai-len kpi-xem-cong

if [[ -f "${CAPITOL_STATE_DIR}/van_tay_18402.env" ]]; then
  VAN_TAY_MA="$(awk -F= '/^VAN_TAY_MA=/{print $2}' "${CAPITOL_STATE_DIR}/van_tay_18402.env" | tr -d '\r')"
  if [[ -n "${VAN_TAY_MA}" ]]; then
    curl -fsS --max-time 5 -H "X-Van-Tay-Ma: ${VAN_TAY_MA}" "http://127.0.0.1:18402/van-tay" | head -c 300 || true
    echo
  fi
fi

echo "== 4. Nạp workflow mẫu KPI nếu có script tao_workflow_mau_kpi.py =="
if [[ -f "${ROOT_DIR}/kpi_capitol/tao_workflow_mau_kpi.py" ]]; then
  python3 -I "${ROOT_DIR}/kpi_capitol/tao_workflow_mau_kpi.py" || echo "CẢNH BÁO: bỏ qua nạp workflow mẫu vì platform-api chưa chạy."
fi

echo "OK: cụm dịch vụ KPI đã sẵn sàng."
