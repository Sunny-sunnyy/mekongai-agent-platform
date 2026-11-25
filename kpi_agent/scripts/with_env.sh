#!/usr/bin/env bash
# Nạp biến môi trường từ .env vào riêng tiến trình con (hỗ trợ cả chạy độc lập lẫn trong mekongai-agent-platform).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KPI_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE=""
for candidate in "$KPI_ROOT/.env" "$KPI_ROOT/../.env" "$KPI_ROOT/../mekongai-rag/.env"; do
  if [ -f "$candidate" ]; then
    ENV_FILE="$candidate"
    break
  fi
done
if [ -n "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
exec "$@"
