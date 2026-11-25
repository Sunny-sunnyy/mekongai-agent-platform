#!/usr/bin/env bash
# Script khởi tạo lần đầu cho máy mới (idempotent, không ghi đè khóa đã có).
# Tạo thư mục trạng thái (CAPITOL_STATE_DIR), thư mục chạy (CAPITOL_RUNTIME_DIR),
# sao chép tệp .env mẫu, sinh mã xác thực nội bộ ngẫu nhiên và tạo file trần ngày.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${CHAY_THU_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
CAPITOL_STATE_DIR="${CAPITOL_STATE_DIR:-${HOME}/.local/share/capitol-platform}"
CAPITOL_RUNTIME_DIR="${CAPITOL_RUNTIME_DIR:-${ROOT_DIR}/runtime}"
ENV_MAU_DIR="${ENV_MAU_DIR:-${SCRIPT_DIR}/../env_mau}"
DAILY_CAP_USD="${DAILY_CAP_USD:-2.0}"

if [[ ! -d "${ENV_MAU_DIR}" ]]; then
  echo "LỖI: không thấy thư mục mẫu ${ENV_MAU_DIR}" >&2
  exit 1
fi

echo "== 1. Tạo thư mục trạng thái: ${CAPITOL_STATE_DIR} =="
mkdir -p "${CAPITOL_STATE_DIR}"/{tran_ngay,bedrock_046,kho_tai_lieu,trang_du_lieu}
chmod 700 "${CAPITOL_STATE_DIR}" "${CAPITOL_STATE_DIR}/bedrock_046"

echo "== 2. Tạo thư mục dữ liệu chạy: ${CAPITOL_RUNTIME_DIR} =="
mkdir -p "${CAPITOL_RUNTIME_DIR}"/{so_cong,so_cong_doc,e2b_so,evals_kho,eval_service,exa_so,kiem_so_kpi,kpi_store/uploads,kpi_van_tay,xuat_file,sandbox_file_tai,giao_dien,s5}
mkdir -p "${CAPITOL_RUNTIME_DIR}/sandbox_hang_doi"/{vao,ra}
mkdir -p "${CAPITOL_RUNTIME_DIR}/hang_doi_phien/vao/_mo" "${CAPITOL_RUNTIME_DIR}/hang_doi_phien/ra/_mo"
chmod 700 "${CAPITOL_RUNTIME_DIR}/evals_kho"

mkdir -p "${ROOT_DIR}/env"

sinh_hex_32() {
  python3 -I -c "import secrets; print(secrets.token_hex(32))"
}

chep_mau_neu_thieu() {
  local src="$1"
  local dst="$2"
  local perm="$3"
  if [[ ! -e "${dst}" ]]; then
    cp "${src}" "${dst}"
    chmod "${perm}" "${dst}"
    echo "  + Tạo mới: ${dst} (chmod ${perm})"
  else
    echo "  = Giữ nguyên đã có: ${dst}"
  fi
}

echo "== 3. Sao chép các tệp môi trường mẫu vào ${ROOT_DIR}/env/ =="
for f in "${ENV_MAU_DIR}"/*.env.example; do
  [[ -e "${f}" ]] || continue
  base="$(basename "${f}" .example)"
  perm="644"
  if [[ "${base}" == *.khoa.env ]]; then
    perm="600"
  fi
  chep_mau_neu_thieu "${f}" "${ROOT_DIR}/env/${base}" "${perm}"
done

echo "== 4. Sao chép các tệp xác thực nội bộ vào ${CAPITOL_STATE_DIR}/ =="
if [[ -d "${ENV_MAU_DIR}/state_dir_mau" ]]; then
  for f in "${ENV_MAU_DIR}/state_dir_mau"/*.env.example; do
    [[ -e "${f}" ]] || continue
    base="$(basename "${f}" .example)"
    chep_mau_neu_thieu "${f}" "${CAPITOL_STATE_DIR}/${base}" "600"
  done
  if [[ -f "${ENV_MAU_DIR}/state_dir_mau/bedrock-046-creds.json.example" ]]; then
    chep_mau_neu_thieu "${ENV_MAU_DIR}/state_dir_mau/bedrock-046-creds.json.example" "${CAPITOL_STATE_DIR}/bedrock_046/bedrock-046-creds.json" "600"
  fi
fi

echo "== 5. Sinh mã bí mật nội bộ thay thế placeholder (nếu còn chữ DIEN_...) =="
python3 -I - "${ROOT_DIR}/env" "${CAPITOL_STATE_DIR}" <<'PYEOF'
import pathlib, re, secrets, sys

env_dir = pathlib.Path(sys.argv[1])
state_dir = pathlib.Path(sys.argv[2])

internal_files = [
    env_dir / "cong_18300.khoa.env",
    env_dir / "kpi.khoa.env",
    env_dir / "mcp_shop.khoa.env",
    env_dir / "minio.khoa.env",
    state_dir / "rag_cong.env",
    state_dir / "rag_nap.env",
    state_dir / "tai_len_18402.env",
    state_dir / "van_tay_18402.env",
    state_dir / "xem_18403.env",
    state_dir / "minio_eval.env",
]

placeholder_rx = re.compile(r"^([A-Z0-9_]+)=(DIEN_[A-Z0-9_]+|THAY_[A-Z0-9_]+)\s*$")
for path in internal_files:
    if not path.is_file():
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = False
    out = []
    for ln in lines:
        m = placeholder_rx.match(ln)
        if m:
            key = m.group(1)
            val = secrets.token_urlsafe(24) if "PASSWORD" in key else secrets.token_hex(32)
            out.append(f"{key}={val}")
            changed = True
        else:
            out.append(ln)
    if changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"  * Đã tự sinh mã nội bộ trong: {path}")
PYEOF

for kf in "mo_trang_18400.txt" "xem_phien_18403.key"; do
  if [[ ! -f "${CAPITOL_STATE_DIR}/${kf}" ]]; then
    sinh_hex_32 > "${CAPITOL_STATE_DIR}/${kf}"
    chmod 600 "${CAPITOL_STATE_DIR}/${kf}"
    echo "  + Tạo mã ngẫu nhiên: ${CAPITOL_STATE_DIR}/${kf}"
  fi
done

echo "== 6. Khởi tạo trần chi tiêu hôm nay ($(date +%F)) =="
TRAN_FILE="${CAPITOL_STATE_DIR}/tran_ngay/$(date +%F).txt"
if [[ ! -f "${TRAN_FILE}" ]]; then
  printf '%s\n' "${DAILY_CAP_USD}" > "${TRAN_FILE}"
  chmod 600 "${TRAN_FILE}"
  echo "  + Tạo mới trần ngày: ${TRAN_FILE} = ${DAILY_CAP_USD} USD"
else
  echo "  = Giữ trần ngày hiện có: ${TRAN_FILE}"
fi

echo "== 7. Kiểm tra tệp giao diện web đã build =="
if [[ -f "${ROOT_DIR}/giao_dien/web/dist/index.html" ]]; then
  echo "  OK: thấy giao_dien/web/dist/index.html"
else
  echo "  LƯU Ý: chưa thấy giao_dien/web/dist/index.html — cần chạy 'npm ci && npm run build' trong giao_dien/web/ trước khi mở giao diện."
fi

echo
echo "Hoàn tất khởi tạo lần đầu. Bước tiếp theo cần người vận hành điền khóa dịch vụ ngoài (OpenRouter/Bedrock/OpenAI/E2B/Exa) vào các tệp *.khoa.env trong ${ROOT_DIR}/env/."
