#!/usr/bin/env bash
# Cổng trung gian RAG chạy trên host WSL: lắng nghe 127.0.0.1:19453 -> chuyển tới mekongai-rag 127.0.0.1:1945.
#   may_chu -> http://127.0.0.1:19453 (RAG_CONG_GOC)
#   ab-*    -> http://host.docker.internal:19453/api/v1/search (RAG_CONG_URL)
set -euo pipefail
cd "$(dirname "$0")/.."
STATE_DIR="${CAPITOL_STATE_DIR:-$HOME/.local/share/capitol-platform}"
ENV_DIR="${CAPITOL_ENV_DIR:-$STATE_DIR/env}"
if [[ ! -f "$ENV_DIR/rag_cong.env" && -f "./env/rag_cong.env" ]]; then
  ENV_DIR="./env"
fi
if ss -ltn 2>/dev/null | grep -q '127.0.0.1:19453 '; then
  echo "DỪNG: 127.0.0.1:19453 đã có tiến trình nghe (cổng đang chạy?)"
  exit 1
fi
set -a
. "$ENV_DIR/rag_cong.env"
. "$ENV_DIR/rag_nap.env"
set +a
export CAPITOL_STATE_DIR="$STATE_DIR"
export NGHE_TREN="${NGHE_TREN:-127.0.0.1:19453}"
export RAG_APP_HOST="${RAG_APP_HOST:-127.0.0.1}"
export RAG_APP_PORT="${RAG_APP_PORT:-1945}"
exec timeout 36000 nice -n 5 python3 -I va_rag/rag_cong.py
