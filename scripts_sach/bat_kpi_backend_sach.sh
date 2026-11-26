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
KPI_PROJ=()
if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
  KPI_PROJ=(-p "${COMPOSE_PROJECT_NAME}")
fi
docker compose "${KPI_PROJ[@]}" --project-directory "${ROOT_DIR}" -f "${KPI_BACKEND_YAML}" up -d kpi-backend
kpi_ok=0
for _ in $(seq 1 60); do
  if docker exec capitol-kpi-backend python -I -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8110/api/meta', timeout=3).status == 200 else 1)" >/dev/null 2>&1; then
    echo "OK: kpi-backend sẵn sàng (http://kpi-backend:8110/api/meta)"
    kpi_ok=1
    break
  fi
  sleep 1
done
if [[ "${kpi_ok}" != "1" ]]; then
  echo "LỖI: hết thời gian chờ kpi-backend tại http://kpi-backend:8110/api/meta" >&2
  exit 1
fi

echo "== 2. Kiểm tra danh sách công cụ và thông tin tĩnh của kpi-backend qua mạng nội bộ =="
CHECK_CTR="capitol-kpi-backend"
CHECK_HOST="127.0.0.1"
if docker ps --format '{{.Names}}' | grep -qx 'capitol-ab-worker-temporal'; then
  CHECK_CTR="capitol-ab-worker-temporal"
  CHECK_HOST="kpi-backend"
fi
docker exec "${CHECK_CTR}" python -I -c "import urllib.request; print(urllib.request.urlopen('http://${CHECK_HOST}:8110/api/tools', timeout=5).read().decode()[:300])"
docker exec "${CHECK_CTR}" python -I -c "import urllib.request; print(urllib.request.urlopen('http://${CHECK_HOST}:8110/api/meta', timeout=5).read().decode())" | tee "${CAPITOL_RUNTIME_DIR}/kpi_van_tay/meta_backend.json"

echo "== 3. Bật kpi-tai-len (18402) =="
docker compose "${KPI_PROJ[@]}" --project-directory "${ROOT_DIR}" -f "${ROOT_DIR}/compose.kpi_tai_len.yaml" up -d kpi-tai-len

if [[ -n "${KPI_EMPTY_PERIOD:-}" && -f "${CAPITOL_STATE_DIR}/tai_len_18402.env" ]]; then
  TAI_LEN_MA="$(awk -F= '/^TAI_LEN_MA=/{print $2}' "${CAPITOL_STATE_DIR}/tai_len_18402.env" | tr -d '\r')"
  if [[ -n "${TAI_LEN_MA}" ]]; then
    for _ in $(seq 1 15); do
      if curl -fsS --max-time 5 -H "X-Tai-Len-Ma: ${TAI_LEN_MA}" "http://127.0.0.1:18402/ky/${KPI_EMPTY_PERIOD}" | head -c 300; then
        echo
        break
      fi
      sleep 1
    done
  fi
elif [[ -f "${CAPITOL_STATE_DIR}/van_tay_18402.env" ]]; then
  VAN_TAY_MA="$(awk -F= '/^VAN_TAY_MA=/{print $2}' "${CAPITOL_STATE_DIR}/van_tay_18402.env" | tr -d '\r')"
  if [[ -n "${VAN_TAY_MA}" ]]; then
    curl -fsS --max-time 5 -H "X-Van-Tay-Ma: ${VAN_TAY_MA}" "http://127.0.0.1:18402/van-tay" | head -c 300 || true
    echo
  fi
fi

echo "== 4. Nạp workflow mẫu KPI nếu có script tao_workflow_mau_kpi.py =="
if [[ -z "${KPI_EMPTY_PERIOD:-}" && -f "${ROOT_DIR}/kpi_capitol/tao_workflow_mau_kpi.py" ]]; then
  python3 -I "${ROOT_DIR}/kpi_capitol/tao_workflow_mau_kpi.py" || echo "CẢNH BÁO: bỏ qua nạp workflow mẫu vì platform-api chưa chạy."
elif [[ -n "${KPI_EMPTY_PERIOD:-}" ]]; then
  echo "  (Bỏ qua nạp 30 mẫu KPI ở chế độ kỳ trống — nạp sau khi tải đủ file dữ liệu kỳ ${KPI_EMPTY_PERIOD}.)"
fi

echo "OK: cụm dịch vụ KPI đã sẵn sàng."
