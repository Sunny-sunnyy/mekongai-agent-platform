#!/usr/bin/env bash
# Bật / tắt / xem cả 3 phần của trợ lý KPI bậc 1 (docs/SPEC.md mục 3): backend chỉ đọc · agent · trang web. Chỉ nghe 127.0.0.1.
# Dùng (từ gốc repo):
#   bash kpi_agent/scripts/chay_ban1.sh start    # bật 3 phần chạy nền, ghi pid + log vào kpi_agent/data/run_logs/
#   bash kpi_agent/scripts/chay_ban1.sh status   # cổng nào đang nghe, pid đã ghi còn sống không
#   bash kpi_agent/scripts/chay_ban1.sh stop     # tắt ĐÚNG các pid script này đã ghi (không đụng tiến trình khác) + xoá file mã phiên
#   bash kpi_agent/scripts/chay_ban1.sh link     # in lại ĐƯỜNG MỞ TRANG có mã phiên (tab mới / trình duyệt khác phải mở bằng đường này)
# MÃ PHIÊN (12/11 — kpi_agent/session_key.py): hộp Docker trên máy gọi được cổng "chỉ nghe 127.0.0.1" ⇒ backend + agent đòi mã ở MỌI lời gọi.
# `start` sinh mã mới vào file quyền 600 NGOÀI repo ($XDG_RUNTIME_DIR/kpi_agent/ hoặc ~/.config/kpi_agent/), in MỘT dòng đường mở có ?k=<mã>;
# trang giữ mã trong tab đó và xoá khỏi thanh địa chỉ. Mã không vào log, không vào kpi_agent/data/. `status` không in mã.
# Tab "Theo dõi" đánh dấu ô lệch trang theo dõi của đội khi đặt KPI_TRACKING_PAGE=<file saved_resource.html trên máy> (không đặt ⇒ chỉ ghi lệch đã biết).
# Cổng đổi bằng biến môi trường (mặc định 8110 / 8120 / 5183), vd thử song song:
#   KPI_BACKEND_PORT=8111 KPI_AGENT_PORT=8121 KPI_WEB_PORT=5184 bash kpi_agent/scripts/chay_ban1.sh start
# Khoá API cho agent: nạp từ .env vào riêng tiến trình agent qua kpi_agent/scripts/with_env.sh (không in, không ghi ra file khác).
# Bật agent KHÔNG gọi mô hình (chỉ đọc /api/meta của backend); tiền chỉ phát sinh khi có người hỏi trong khung chat.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
BACKEND_PORT="${KPI_BACKEND_PORT:-8110}"
AGENT_PORT="${KPI_AGENT_PORT:-8120}"
WEB_PORT="${KPI_WEB_PORT:-5183}"
LOG_DIR="$REPO/kpi_agent/data/run_logs"   # Git bỏ qua (kpi_agent/data/) — log có thể chứa câu hỏi + mã NV
mkdir -p "$LOG_DIR"
KEY_DIR="${XDG_RUNTIME_DIR:-$HOME/.config}/kpi_agent"   # cùng chỗ với kpi_agent/session_key.py key_dir()
KEY_FILE="$KEY_DIR/session_${BACKEND_PORT}.key"

open_link() {   # đường mở trang có mã — chỗ DUY NHẤT script in mã (ra màn hình người bật, không ra file log)
  echo "MỞ TRANG BẰNG ĐƯỜNG NÀY (có mã phiên — đừng dán vào chỗ khác): http://127.0.0.1:$WEB_PORT/?k=$(cat "$KEY_FILE")"
}

pid_file() { echo "$LOG_DIR/$1_$2.pid"; }        # vd backend_8110.pid — mỗi bộ cổng một bộ pid, chạy song song không lẫn
listening() { ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN; }

wait_http() {   # chờ tới khi URL trả HTTP 200, tối đa $2 giây; $3 = "key" ⇒ gửi kèm mã phiên
  local url="$1" seconds="$2" with_key="${3:-}"
  for _ in $(seq "$seconds"); do
    if [ "$with_key" = key ]; then
      # mã đi qua file cấu hình tạm của curl (printf là lệnh trong của bash) ⇒ không nằm trên dòng lệnh của tiến trình nào
      curl -sf -o /dev/null -m 2 -K <(printf 'header = "X-Kpi-Session: %s"\n' "$(cat "$KEY_FILE")") "$url" && return 0
    else
      curl -sf -o /dev/null -m 2 "$url" && return 0
    fi
    sleep 1
  done
  return 1
}

wait_port() {   # chờ tới khi cổng $1 có tiến trình nghe, tối đa $2 giây
  for _ in $(seq "$2"); do listening "$1" && return 0; sleep 1; done
  return 1
}

start_one() {   # $1 tên, $2 cổng, còn lại = lệnh. setsid ⇒ tiến trình có nhóm riêng (pid = mã nhóm) ⇒ stop tắt được cả tiến trình con
  local name="$1" port="$2"; shift 2
  if listening "$port"; then
    echo "LỖI: cổng $port đã có tiến trình khác nghe — không bật $name (đổi cổng bằng biến môi trường)." >&2
    return 1
  fi
  setsid "$@" > "$LOG_DIR/${name}_${port}.log" 2>&1 < /dev/null &
  echo $! > "$(pid_file "$name" "$port")"
  echo "đã bật $name :$port (pid $!, log kpi_agent/data/run_logs/${name}_${port}.log)"
}

stop_one() {    # chỉ tắt pid script đã ghi, và chỉ khi lệnh của pid đó đúng là lệnh có cổng này (tránh tắt nhầm khi pid bị hệ thống dùng lại)
  local name="$1" port="$2" file pid
  file="$(pid_file "$name" "$port")"
  [ -f "$file" ] || { echo "$name :$port — không có pid đã ghi (không tắt gì)"; return 0; }
  pid="$(cat "$file")"
  if kill -0 "$pid" 2>/dev/null && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q -- "$port"; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid"
    for _ in $(seq 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    echo "đã tắt $name :$port (pid $pid)"
  else
    echo "$name :$port — pid $pid không còn chạy / không phải tiến trình của script (không tắt)"
  fi
  rm -f "$file"
}

case "${1:-}" in
  start)
    cd "$REPO"
    # mã phiên MỚI mỗi lần bật: thư mục 700, file 600 (umask 077), ≥ 32 ký tự ngẫu nhiên từ `secrets`
    ( umask 077; mkdir -p "$KEY_DIR"; chmod 700 "$KEY_DIR"; .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))' > "$KEY_FILE" )
    # Bộ cổng THỬ (khác 8110) không được ghi vào kho thật của người dùng (kpi_agent/data/kpi_store.sqlite — mỗi lần bật thêm 1 dòng lượt chạy;
    # C thấy 12/11): không đặt KPI_STORE ⇒ tự dùng kho riêng theo cổng, nằm cạnh log (Git bỏ qua). Bộ mặc định 8110 dùng kho thật như cũ.
    if [ "$BACKEND_PORT" != 8110 ] && [ -z "${KPI_STORE:-}" ]; then
      export KPI_STORE="$LOG_DIR/kpi_store_thu_${BACKEND_PORT}.sqlite"
      echo "bộ cổng thử ($BACKEND_PORT): dùng kho riêng kpi_agent/data/run_logs/kpi_store_thu_${BACKEND_PORT}.sqlite (không đụng kho thật)"
    fi
    export KPI_SESSION_KEY_FILE="$KEY_FILE"   # backend + agent đọc file này lúc bật; không truyền giá trị mã qua biến môi trường
    unset KPI_SESSION_KEY                      # không để biến "off" của phiên làm việc (test) lọt vào bộ chạy thật
    # 10/11 (A: workflow chính chuyển sang RAW khi khớp — C so mù trang sếp: tiền 112/112): mặc định đường RAW DATA, ngày chốt 31/10.
    # Quay về đường sổ Tracking (ảnh chụp 20/10): KPI_WORKFLOW=kpi_agent/workflows/kpi_thuong_nvbh.yaml KPI_CUTOFF=2026-10-20 bash … start
    # Giới hạn: trang kéo–thả chưa chạy thử được workflow RAW (file MCP ở kpi_agent/data/ — không mở thư mục đó cho trang chọn file).
    if [ -n "${KPI_XEM_URL:-}" ]; then
      # Chế độ "MỘT NGUỒN SỐ" (12/11 — kpi_agent/agent/xem_proxy.py): KPI_XEM_URL=<cổng xem của hộp KPI trong Capitol, vd http://127.0.0.1:18403>
      # ⇒ KHÔNG bật backend trên máy; trang gọi /api/* vào agent, agent đọc số qua cổng xem (mã dịch vụ ở ${CAPITOL_STATE_DIR:-~/.local/share/capitol-platform}/, agent tự đọc).
      # Trang chỉ xem: soát + tải file làm ở xưởng (đặt VITE_KPI_WORKSHOP_URL để có link). Mã phiên của trang vẫn như cũ.
      echo "chế độ một nguồn số: đọc số qua $KPI_XEM_URL — không bật backend :$BACKEND_PORT trên máy"
      API_TARGET="http://127.0.0.1:$AGENT_PORT"
      export VITE_KPI_READ_ONLY=1
    else
    # 13/11: tính sẵn 1 % gia vị lúc bật (mặc định BẬT ở script này; tắt: KPI_WARM_SPICE=off) — không tính sẵn thì câu hỏi gia vị ĐẦU TIÊN sau khi bật
    # hết giờ (lần gọi đầu ≈ 15–35 s > hạn chờ 10 s của trợ lý). Cái giá: bật chậm thêm ≈ 15–35 s.
    export KPI_WARM_SPICE="${KPI_WARM_SPICE:-on}"
    [ "$KPI_WARM_SPICE" = on ] && echo "đang bật backend + tính sẵn Thưởng thêm và 1 % gia vị (có thể mất 1–2 phút)…"
    KPI_WORKFLOW="${KPI_WORKFLOW:-kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml}" KPI_CUTOFF="${KPI_CUTOFF:-2026-10-31}" \
      start_one backend "$BACKEND_PORT" .venv/bin/uvicorn kpi_agent.backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
    # 180 s: đường RAW tính sẵn Thưởng thêm (đo 11/11: bật 16 ⇒ 33 s) + 1 % gia vị (đo 13/11 máy đang bận: 37 ⇒ 72 s) lúc bật — hạn ngắn làm script báo
    # lỗi dù backend đang lên
    wait_http "http://127.0.0.1:$BACKEND_PORT/api/meta" 180 key || { echo "LỖI: backend không lên sau 180 s — xem log" >&2; exit 1; }
      API_TARGET="http://127.0.0.1:$BACKEND_PORT"
    fi
    # Origin được gửi câu hỏi = đúng trang web của bộ cổng này (agent chặn Origin lạ + chỉ nhận JSON — tab Theo dõi bản 2, 11/11)
    KPI_ALLOWED_ORIGINS="http://127.0.0.1:$WEB_PORT,http://localhost:$WEB_PORT" \
    KPI_BACKEND_URL="http://127.0.0.1:$BACKEND_PORT" start_one agent "$AGENT_PORT" \
      kpi_agent/scripts/with_env.sh .venv/bin/uvicorn kpi_agent.agent.server:app --host 127.0.0.1 --port "$AGENT_PORT"
    # agent chỉ có POST /api/chat (P2c tắt /openapi.json) ⇒ chờ cổng nghe, KHÔNG gửi câu hỏi thử (mỗi câu tốn tiền mô hình)
    wait_port "$AGENT_PORT" 30 || { echo "LỖI: agent không lên sau 30 s — xem log" >&2; exit 1; }
    cd "$REPO/kpi_agent/dashboard"
    [ -d node_modules ] || { echo "LỖI: chưa cài thư viện trang — chạy 'npm ci' trong kpi_agent/dashboard/ trước" >&2; exit 1; }
    KPI_API="$API_TARGET" KPI_AGENT="http://127.0.0.1:$AGENT_PORT" start_one web "$WEB_PORT" \
      node_modules/.bin/vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort
    wait_http "http://127.0.0.1:$WEB_PORT/" 30 || { echo "LỖI: trang không lên sau 30 s — xem log" >&2; exit 1; }
    echo "XONG (tắt: bash kpi_agent/scripts/chay_ban1.sh stop, cùng biến cổng; in lại đường mở: … link)"
    open_link
    ;;
  stop)
    stop_one web "$WEB_PORT"; stop_one agent "$AGENT_PORT"; stop_one backend "$BACKEND_PORT"
    rm -f "$KEY_FILE"   # mã chỉ sống cùng một lần bật
    ;;
  link)
    [ -f "$KEY_FILE" ] || { echo "Chưa có mã phiên cho bộ cổng này — chưa bật, hoặc đã stop (bash kpi_agent/scripts/chay_ban1.sh start)" >&2; exit 1; }
    open_link
    ;;
  status)
    for item in "backend $BACKEND_PORT" "agent $AGENT_PORT" "web $WEB_PORT"; do
      set -- $item
      file="$(pid_file "$1" "$2")"
      pid="$( [ -f "$file" ] && cat "$file" || echo "—")"
      alive="$( [ "$pid" != "—" ] && kill -0 "$pid" 2>/dev/null && echo "còn chạy" || echo "không")"
      echo "$1 :$2 — cổng $(listening "$2" && echo "ĐANG nghe" || echo "không nghe") · pid đã ghi $pid ($alive)"
    done
    ;;
  *)
    echo "Dùng: bash kpi_agent/scripts/chay_ban1.sh start | stop | status | link   (cổng: KPI_BACKEND_PORT, KPI_AGENT_PORT, KPI_WEB_PORT)" >&2
    exit 2
    ;;
esac
