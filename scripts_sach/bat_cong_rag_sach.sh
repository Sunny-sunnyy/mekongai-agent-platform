#!/usr/bin/env bash
# Bật cổng trung chuyển RAG trên máy chủ (WSL/Linux) tại 127.0.0.1:19453.
# Cổng này đọc khóa từ CAPITOL_STATE_DIR/rag_cong.env và rag_nap.env,
# chuyển tiếp truy vấn từ container ab-* sang app RAG nội bộ ở 127.0.0.1:1945.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${CHAY_THU_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
CAPITOL_STATE_DIR="${CAPITOL_STATE_DIR:-${HOME}/.local/share/capitol-platform}"
CAPITOL_RUNTIME_DIR="${CAPITOL_RUNTIME_DIR:-${ROOT_DIR}/runtime}"

RAG_CONG_ENV="${CAPITOL_STATE_DIR}/rag_cong.env"
RAG_NAP_ENV="${CAPITOL_STATE_DIR}/rag_nap.env"
RAG_SCRIPT="${ROOT_DIR}/va_rag/rag_cong.py"
LOG_FILE="${CAPITOL_RUNTIME_DIR}/rag_cong.log"
PID_FILE="${CAPITOL_RUNTIME_DIR}/rag_cong.pid"

mkdir -p "${CAPITOL_RUNTIME_DIR}"

if [[ ! -f "${RAG_SCRIPT}" ]]; then
  echo "LỖI: không thấy ${RAG_SCRIPT}" >&2
  exit 1
fi

if [[ ! -f "${RAG_CONG_ENV}" || ! -f "${RAG_NAP_ENV}" ]]; then
  echo "LỖI: thiếu ${RAG_CONG_ENV} hoặc ${RAG_NAP_ENV}. Hãy chạy khoi_tao_lan_dau.sh trước." >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "${RAG_CONG_ENV}"
# shellcheck source=/dev/null
source "${RAG_NAP_ENV}"
set +a

export NGHE_TREN="${NGHE_TREN:-127.0.0.1:19453}"
export RAG_APP_HOST="${RAG_APP_HOST:-127.0.0.1}"
export RAG_APP_PORT="${RAG_APP_PORT:-1945}"
export CAPITOL_STATE_DIR
export CAPITOL_RUNTIME_DIR

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "Cổng RAG đã chạy sẵn (PID=${OLD_PID}) tại ${NGHE_TREN}."
    exit 0
  fi
fi

echo "Khởi động cổng RAG tại ${NGHE_TREN} (log: ${LOG_FILE})..."
nohup python3 -I "${RAG_SCRIPT}" >"${LOG_FILE}" 2>&1 &
NEW_PID=$!
echo "${NEW_PID}" > "${PID_FILE}"

for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null "http://${NGHE_TREN}/health" 2>/dev/null || ss -ltn | grep -q ":19453"; then
    echo "OK: cổng RAG sẵn sàng (PID=${NEW_PID}, ${NGHE_TREN})."
    exit 0
  fi
  sleep 0.5
done

echo "CẢNH BÁO: cổng RAG chưa mở sau 10 giây; kiểm tra ${LOG_FILE}." >&2
exit 1
