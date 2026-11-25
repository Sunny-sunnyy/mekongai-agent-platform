#!/usr/bin/env bash
# Tắt toàn bộ container và tiến trình nền của nền tảng một cách an toàn.
# Không dùng cờ -v để giữ nguyên dữ liệu DB, MinIO và sổ chi tiêu.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_chung.sach.sh
source "${SCRIPT_DIR}/_chung.sach.sh"

echo "== 1. Tắt cụm container kpi-backend (nếu đang chạy) =="
if [[ -f "${SCRIPT_DIR}/compose.kpi_backend.sach.yaml" ]]; then
  docker compose --project-directory "${ROOT_DIR}" -f "${SCRIPT_DIR}/compose.kpi_backend.sach.yaml" stop || true
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
