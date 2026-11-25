#!/usr/bin/env bash
# Kiểm tra nhanh 0 đồng cổng RAG trên WSL (127.0.0.1:19453 -> 127.0.0.1:1945).
set -uo pipefail
cd "$(dirname "$0")/.."
STATE_DIR="${CAPITOL_STATE_DIR:-$HOME/.local/share/capitol-platform}"
ENV_DIR="${CAPITOL_ENV_DIR:-$STATE_DIR/env}"
RUNTIME_DIR="${CAPITOL_RUNTIME_DIR:-./runtime}"
if [[ ! -f "$ENV_DIR/rag_cong.env" && -f "./env/rag_cong.env" ]]; then
  ENV_DIR="./env"
fi
d=0; h=0; dat() { echo "  ĐẠT   $*"; d=$((d+1)); }; hong() { echo "  HỎNG  $*"; h=$((h+1)); }
c=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:19453/api/v1/kho)
[[ "$c" == 401 ]] && dat "K1 WSL -> 127.0.0.1:19453 không mã -> 401" || hong "K1 không mã: '$c' (000 -> cổng chưa chạy)"
l=$(ss -ltn 2>/dev/null | awk '$4 ~ /:19453$/ {print $4}' | tr '\n' ' ')
[[ "$l" == "127.0.0.1:19453 " ]] && dat "K2 chỉ nghe 127.0.0.1:19453" || hong "K2 địa chỉ nghe: '$l'"
o=$(cat "$RUNTIME_DIR/org_id.txt")
r=$( set -a; . "$ENV_DIR/rag_cong.env"; . "$ENV_DIR/rag_nap.env"; set +a
     RAG_CONG_GOC=http://127.0.0.1:19453 python3 -I - "$o" <<'PY_CHECK'
import sys; sys.path.insert(0, "giao_dien")
import goi_cong_rag as G
org = sys.argv[1]; uid = G._uid(org)
c, d = G.tao_goi_app(org)("GET", f"/api/v1/qdrant/collections/user/{uid}", timeout=30)
print("K3", c, type((d or {}).get("data")).__name__)
c2, d2 = G.tao_goi_app(org, ma_nap="x" * 40)("POST", "/api/v1/upload-files", form={"user_id": uid, "language": "vie+eng", "force_reindex": "false"}, tep=("thu.txt", b"thu"), timeout=30)
print("K4", c2, bool(d2.get("_cong_tu_choi")))
PY_CHECK
)
echo "$r" | grep -q "^K3 200 list" && dat "K3 đường may_chu có mã -> GET kho 200 (cổng tới app 127.0.0.1:1945)" || hong "K3: $(echo "$r" | grep ^K3) (503 -> app mekongai-rag chưa bật)"
echo "$r" | grep -q "^K4 401 True" && dat "K4 nạp mã sai -> cổng từ chối trước app" || hong "K4: $(echo "$r" | grep ^K4)"
c=$(docker exec -e O="$o" capitol-ab-api python -I -c "
import os,urllib.request,urllib.error
def g(h):
    try: return urllib.request.urlopen(urllib.request.Request('http://host.docker.internal:19453/api/v1/kho', headers=h), timeout=10).status
    except urllib.error.HTTPError as e: return e.code
    except Exception as e: return type(e).__name__
print(g({}), g({'X-Rag-Ma': os.environ.get('RAG_MA',''), 'X-Rag-Org': os.environ['O']}))" 2>/dev/null)
[[ "$c" == "401 200" ]] && dat "K5 ab-api -> host.docker.internal:19453: không mã 401 · có RAG_MA của hộp 200" || hong "K5 ab-api: '$c'"
k=0; for x in capitol-ab-api capitol-ab-worker capitol-ab-worker-temporal; do
  [[ "$(docker exec $x sh -c 'echo $RAG_CONG_URL')" == "http://host.docker.internal:19453/api/v1/search" ]] && k=$((k+1)); done
(( k == 3 )) && dat "K6 3 hộp ab-* có RAG_CONG_URL chuẩn" || echo "  CHƯA  K6 $k / 3 hộp ab-* có RAG_CONG_URL chuẩn"
echo "== $d ĐẠT / $h HỎNG"; (( h == 0 ))
