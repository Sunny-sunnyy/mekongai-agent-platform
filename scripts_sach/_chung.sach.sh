#!/usr/bin/env bash
# Thư viện dùng chung cho chuỗi lệnh khởi động và tắt hệ thống sạch.
# Không gắn cứng tên người dùng hay đường dẫn máy cố định.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${CHAY_THU_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

export CAPITOL_STATE_DIR="${CAPITOL_STATE_DIR:-${HOME}/.local/share/capitol-platform}"
export CAPITOL_RUNTIME_DIR="${CAPITOL_RUNTIME_DIR:-${ROOT_DIR}/runtime}"
export KPI_KHO="${KPI_KHO:-${CAPITOL_RUNTIME_DIR}/kpi_store}"

cd "${ROOT_DIR}"

COMPOSE_FILES=(
  -f "${ROOT_DIR}/compose.yaml"
  -f "${ROOT_DIR}/compose.temporal.yaml"
  -f "${ROOT_DIR}/compose.minio.yaml"
  -f "${ROOT_DIR}/compose.cong_ai.yaml"
  -f "${ROOT_DIR}/compose.cong_ngoai.yaml"
  -f "${ROOT_DIR}/compose.eval.yaml"
  -f "${ROOT_DIR}/compose.hop_phien.yaml"
  -f "${ROOT_DIR}/compose.nhan_file_hop.yaml"
  -f "${ROOT_DIR}/compose.kho_bang.yaml"
  -f "${ROOT_DIR}/compose.dang_trang.yaml"
  -f "${ROOT_DIR}/compose.ci_hop_phien.yaml"
  -f "${ROOT_DIR}/compose.mcp.yaml"
  -f "${ROOT_DIR}/compose.mcp_exa.yaml"
  -f "${ROOT_DIR}/compose.mcp_shop.yaml"
  -f "${ROOT_DIR}/compose.mo_hinh_mac_dinh.yaml"
  -f "${ROOT_DIR}/compose.rao_code.yaml"
  -f "${ROOT_DIR}/compose.khong_ve_may.yaml"
)

if [[ -f "${SCRIPT_DIR}/compose.sach.override.yaml" ]]; then
  COMPOSE_FILES+=(-f "${SCRIPT_DIR}/compose.sach.override.yaml")
fi

dc() {
  docker compose --project-directory "${ROOT_DIR}" "${COMPOSE_FILES[@]}" "$@"
}

can_ram() {
  local min_mb="${1:-4000}"
  if [[ ! -r /proc/meminfo ]]; then
    return 0
  fi
  local avail_kb avail_mb
  avail_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
  avail_mb="$((avail_kb / 1024))"
  if (( avail_mb < min_mb )); then
    echo "CẢNH BÁO: RAM khả dụng chỉ còn ${avail_mb} MiB (khuyến nghị tối thiểu ${min_mb} MiB)." >&2
    return 1
  fi
  echo "OK: RAM khả dụng ${avail_mb} MiB (>= ${min_mb} MiB)."
}

cho_http() {
  local url="$1"
  local ten="${2:-$1}"
  local toi_da="${3:-60}"
  local i=0
  while (( i < toi_da )); do
    if curl -fsS --max-time 3 -o /dev/null "${url}" 2>/dev/null; then
      echo "OK: ${ten} sẵn sàng (${url})"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "LỖI: hết thời gian chờ ${ten} tại ${url}" >&2
  return 1
}

kiem_cong_ban() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    if ss -ltn | awk '{print $4}' | grep -qE "(:|^)${port}$"; then
      return 0
    fi
  fi
  return 1
}
