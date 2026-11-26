#!/usr/bin/env bash
# Tắt toàn bộ container và tiến trình nền của nền tảng một cách an toàn.
# Không dùng cờ -v để giữ nguyên dữ liệu DB, MinIO và sổ chi tiêu.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_chung.sach.sh
source "${SCRIPT_DIR}/_chung.sach.sh"

echo "== 1. Tắt cụm container kpi-backend và kpi-tai-len (nếu đang chạy) =="
KPI_PROJ=()
if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
  KPI_PROJ=(-p "${COMPOSE_PROJECT_NAME}")
fi
if [[ -f "${SCRIPT_DIR}/compose.kpi_backend.sach.yaml" ]]; then
  docker compose "${KPI_PROJ[@]}" --project-directory "${ROOT_DIR}" -f "${SCRIPT_DIR}/compose.kpi_backend.sach.yaml" stop || true
fi
if [[ -f "${ROOT_DIR}/compose.kpi_tai_len.yaml" ]]; then
  docker compose "${KPI_PROJ[@]}" --project-directory "${ROOT_DIR}" -f "${ROOT_DIR}/compose.kpi_tai_len.yaml" stop || true
fi

echo "== 2. Tắt các container nền tảng chính (giữ nguyên volume dữ liệu) =="
dc stop

for pid_name in "rag_cong.pid" "giao_dien.pid"; do
  pid_file="${CAPITOL_RUNTIME_DIR}/${pid_name}"
  if [[ -f "${pid_file}" ]]; then
    pid="$(cat "${pid_file}" || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "  - Dừng tiến trình nền (${pid_name}, PID=${pid})"
      kill "${pid}" || true
    fi
    rm -f "${pid_file}"
  fi
done

echo "OK: đã tắt an toàn toàn bộ dịch vụ."
