# Hướng dẫn cài đặt từ máy trống (`mekongai-agent-platform`)

Tài liệu này hướng dẫn dựng toàn bộ hệ thống từ một máy chưa cài gì, viết theo khuôn **Bước · Lệnh · Thấy gì là đúng · Sai thì làm gì · `DỪNG-CẦN-NGƯỜI`** để cả đồng nghiệp lẫn coding agent có thể thực hiện từng bước chính xác.

- **Môi trường mặc định:** Windows 10/11 + **WSL2 (Ubuntu 22.04/24.04 LTS)** + **Docker Desktop** (WSL2 backend, cấp **≥ 16 GB RAM** cho WSL2, dung lượng đĩa trống ≥ 40 GB; khuyến nghị máy vật lý 32 GB RAM khi chạy đồng thời với `mekongai-rag`).
  - *Cấu hình RAM WSL2 trên Windows:* tạo/sửa file `%USERPROFILE%\.wslconfig` với `memory=16GB` (tối thiểu `14GB` trên máy 16 GB).
- **Ghi chú Linux / macOS:**
  - *Linux (Ubuntu/Debian):* cài Docker Engine + Docker Compose v2 (`docker compose version`), bỏ qua bước WSL2/Docker Desktop; các lệnh bên dưới dùng y nguyên.
  - *macOS (Apple Silicon / Intel):* cấp ≥ 16 GB RAM trong Docker Desktop; 7 file `.tar` gốc Capitol biên dịch cho `linux/amd64` nên bật hỗ trợ Rosetta / nền tảng `linux/amd64` trên Apple Silicon (tốc độ sẽ chậm hơn x86_64).
- **Kho liên kết bắt buộc dựng trước:** `https://github.com/hieumekongai/mekongai-rag` (phải chạy ở `http://127.0.0.1:1945` để sử dụng tính năng hỏi đáp tài liệu/ảnh/bảng tính).

---

## Tổng quan những gì cần lấy ngoài GitHub

Kho GitHub **không chứa** bí mật và 7 file `.tar` nặng (~6,57 GiB). Trước khi bắt đầu, cần lấy từ kênh nội bộ công ty:
1. **Thư mục Google Drive bàn giao** chứa **7 file `.tar` image Docker gốc** (~6,57 GiB), file `SHA256SUMS` và bản báo cáo Word đầy đủ (có Phụ lục C).
2. **API Key dịch vụ ngoài** (tự điền ở Bước 6).

---

## Bước 1 — Chuẩn bị máy nền & công cụ hệ thống

### `DỪNG-CẦN-NGƯỜI` (Mốc 1 — Cài WSL2 & Docker Desktop nếu máy chưa có)
Trên Windows (PowerShell quyền Administrator):
- Bật WSL2 Ubuntu: `wsl --install -d Ubuntu-24.04`
- Cài **Docker Desktop**, bật *Use the WSL 2 based engine* và tích hợp Ubuntu trong *Settings → Resources → WSL Integration*.

### Lệnh kiểm tra và cài đặt trong Ubuntu WSL2
```bash
sudo apt-get update && sudo apt-get install -y poppler-utils curl jq openssl git python3 python3-venv python3-pip
command -v pdfinfo pdftotext docker git python3 node
free -h
docker --version && docker compose version
```

- **Thấy gì là đúng:** `free -h` báo tổng RAM cấp cho WSL ≥ 15 GiB; `docker --version` và `docker compose version` không lỗi; `pdfinfo` và `pdftotext` có sẵn (`/usr/bin/pdfinfo`, `/usr/bin/pdftotext`); `python3` ≥ 3.11; `node` ≥ 20 (nếu chưa có Node.js LTS, cài để phục vụ build giao diện web ở Bước 4).
- **Sai thì làm gì:**
  - `Cannot connect to the Docker daemon`: mở Docker Desktop trên Windows, chờ khởi động xong rồi bật lại WSL Integration.
  - Thiếu `pdfinfo` / `pdftotext` (`poppler-utils`): **bắt buộc cài**, nếu thiếu thì cổng RAG (`19453`) và luồng đọc thẳng PDF sẽ từ chối mọi file PDF.
  - Kiểm tra cổng trống trước khi cài:
    ```bash
    ss -ltn | grep -E ':(1945|6333|8000|8080|8110|8120|5183|18233|18300|18301|18400|18401|18402|18403|19453)\b' || echo "CONG_TRONG"
    ```

---

## Bước 2 — Clone 2 kho GitHub & dựng trước `mekongai-rag`

Trên WSL2, luôn đặt mã nguồn bên trong hệ thống file Linux (`~/mekongai/...`, **không đặt trong `/mnt/c/...`** để tránh chậm I/O và lỗi quyền file):

> *Lưu ý URL kho:* Địa chỉ `https://github.com/hieumekongai/...` dùng sau khi hoàn tất chuyển quyền sở hữu kho từ tài khoản chuyển tiếp sang tài khoản chính thức của công ty (`hieumekongai`). Trong giai đoạn chuyển tiếp trước khi chuyển quyền, thay bằng URL kho private tương ứng được cấp.

```bash
mkdir -p ~/mekongai && cd ~/mekongai
git clone https://github.com/hieumekongai/mekongai-rag.git
git clone https://github.com/hieumekongai/mekongai-agent-platform.git
```

### Dựng và bật `mekongai-rag` trước
Làm theo đúng `README.md` và `docs/` của kho `mekongai-rag` (tạo `.env`, `.env.qdrant`, chạy `docker compose up -d --build`).

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1945/health
```

- **Thấy gì là đúng:** In ra `200`; `GET http://127.0.0.1:6333/healthz` (Qdrant) khoẻ mạnh.
- **Sai thì làm gì:** Xem `docker compose logs` bên thư mục `mekongai-rag` (thường do thiếu khoá Qdrant hoặc API key LLM trong `.env` của `mekongai-rag`).

---

## Bước 3 — Tải và nạp 7 file `.tar` image gốc từ Google Drive

### `DỪNG-CẦN-NGƯỜI` (Mốc 2 — Tải 7 file `.tar` từ Google Drive công ty)
Tải đủ 7 file `.tar` (~7.049.789.056 byte ≈ 6,57 GiB) từ Google Drive công ty về đặt trong thư mục `~/capitol_tars/` (hoặc `luu_image/` của kho, nơi đã có sẵn file `SHA256SUMS`).

### Lệnh kiểm tra SHA-256 và nạp vào Docker
```bash
cd ~/mekongai/mekongai-agent-platform
cp luu_image/SHA256SUMS ~/capitol_tars/SHA256SUMS
(cd ~/capitol_tars && sha256sum -c SHA256SUMS)
```

Chỉ sau khi **7 / 7 dòng báo `: OK`**, chạy nạp vào Docker:

```bash
for f in ~/capitol_tars/*.tar; do
  echo "Đang nạp $f ..."
  docker load -i "$f"
done
docker images | grep capitol-local
```

Danh sách 7 image gốc bắt buộc có:
1. `agentic-backend_8505e4dd93a4.tar` → `capitol-local/agentic-backend:release-4.10.0`
2. `platform-api_e337db773769.tar` → `capitol-local/platform-api:release-1.82.0`
3. `mcp-servers_d2644587fb97.tar` → `capitol-local/mcp-servers:release-3.16.0`
4. `mcp-fastapi_7fa52b49d93d.tar` → `capitol-local/mcp-fastapi:release-3.16.0`
5. `artifact-blob-store_4c0e60c32497.tar` → `capitol-local/artifact-blob-store:release-1.0.2`
6. `eval-service_2bc7aa5adbcf.tar` → `capitol-local/eval-service:release-0.18.4`
7. `qdrant-svc_3fe3ffea6c0e.tar` → `capitol-local/qdrant-svc:release-0.32.0`

- **Thấy gì là đúng:** `sha256sum -c` báo `OK` cả 7 dòng; `docker images | grep capitol-local` hiện đủ 7 image.
- **Sai thì làm gì:**
  - Nếu `sha256sum` báo `FAILED`: file tải bị lỗi giữa chừng ⇒ tải lại đúng file `.tar` đó từ Google Drive, tuyệt đối không nạp file sai checksum.
  - Nếu hết dung lượng đĩa: kiểm tra Docker Desktop disk image size và dọn image thừa không liên quan.

---

## Bước 4 — Tự dựng 3 nhóm image từ `Dockerfile` & build giao diện web

Theo cấu trúc bàn giao, các image tự phát triển được dựng trực tiếp từ `docker_build/` kèm file khoá phiên bản (`uv.lock` 58 gói offline cho `kpi-backend`, `requirements-exa.txt` cho `mcp-exa`, và các gói `sandbox-py`):

```bash
cd ~/mekongai/mekongai-agent-platform

# 1. Dựng 3 image bổ sung
docker build -t capitol-local/kpi-backend:c1b4111a -f docker_build/kpi-backend/Dockerfile docker_build/kpi-backend
docker build -t capitol-local/mcp-exa:latest -f docker_build/mcp-exa/Dockerfile docker_build/mcp-exa
docker build -t capitol-local/sandbox-py:v3 -f docker_build/sandbox-py/v3/Dockerfile.v3 docker_build/sandbox-py/v3

# 2. Build (hoặc kiểm tra) giao diện điều hành web
cd giao_dien/web
npm ci && npm run kiem-kieu && npm run build
cd ../..
test -f giao_dien/web/dist/index.html && echo "WEB_DIST_OK"
```

*(Các image công khai như `postgres:16-alpine`, `redis:7-alpine`, `temporalio/temporal:1.4.1`, `amazon/dynamodb-local:2.5.2`, `otel/opentelemetry-collector:0.111.0`, `caddy:2.11.7`, `bitnamilegacy/minio` đã ghim tag trong các file compose, Docker sẽ tự kéo khi chạy Bước 7).*

- **Thấy gì là đúng:** Cả 3 lệnh `docker build` thoát mã `0`; `npm run kiem-kieu` không lỗi TypeScript; in ra `WEB_DIST_OK`.
- **Sai thì làm gì:** Nếu `docker build` lỗi mạng khi tải gói Python/APT, kiểm tra DNS trong WSL (`/etc/resolv.conf`) hoặc cấu hình proxy trong Docker Desktop.

---

## Bước 5 — Khởi tạo thư mục trạng thái, runtime & sinh 16 file `.env`

Hệ thống sử dụng hai biến thư mục chuẩn (không gắn cứng đường dẫn cá nhân):
- `CAPITOL_STATE_DIR` (mặc định `~/.local/share/capitol-platform`, quyền `700`): chứa khoá nội bộ, trần chi tiêu ngày, thông tin Bedrock STS, kho tài liệu, trạng thái trang Dữ liệu.
- `CAPITOL_RUNTIME_DIR` (mặc định `./runtime`): chứa sổ cổng AI, sổ E2B, kho KPI, vân tay tĩnh, hàng đợi sandbox, file kết quả xuất ra.

```bash
cd ~/mekongai/mekongai-agent-platform
bash scripts_sach/khoi_tao_lan_dau.sh
```

- **Thấy gì là đúng:** Script tạo đủ các thư mục con trong `CAPITOL_STATE_DIR` và `CAPITOL_RUNTIME_DIR`, sao chép đủ **16 file `.env`** từ `env_mau/` vào `env/` (gồm 4 file cấu hình nhóm A, 4 file khoá nội bộ nhóm B tự sinh ngẫu nhiên với quyền `600`, và 8 file khoá dịch vụ ngoài nhóm C), tạo sẵn các file bí mật nội bộ trong `CAPITOL_STATE_DIR` (`rag_cong.env`, `rag_nap.env`, `tai_len_18402.env`, `van_tay_18402.env`, `xem_18403.env`, `minio_eval.env`, `mo_trang_18400.txt`, `xem_phien_18403.key`) và file trần ngày hôm nay (`tran_ngay/$(date +%F).txt`).
- **Sai thì làm gì:** Nếu báo lỗi `\r` (CRLF) do từng mở script trên Windows, chạy `sed -i 's/\r$//' scripts_sach/*.sh` rồi chạy lại.

---

## Bước 6 — Điền API Key dịch vụ ngoài & đặt trần chi tiêu ngày

### `DỪNG-CẦN-NGƯỜI` (Mốc 3 — Điền API Key vào `.env`, tuyệt đối không commit lên Git)
Mở các file sau trong `env/` và `${CAPITOL_STATE_DIR}/` bằng trình soạn thảo cục bộ để thay các nhãn `DIEN_...` bằng khoá thật (giữ quyền `chmod 600`):

| File cần điền | Biến / nội dung | Bắt buộc? | Lấy key ở đâu & Ghi chú |
|---|---|---|---|
| `env/agentic-backend.khoa.env` | `OPENAI_API_KEY` (tuỳ chọn `SERPER_API_KEY`) | **Bắt buộc** (`OPENAI_API_KEY`) | Lấy `OPENAI_API_KEY` tại `https://platform.openai.com/api-keys` (chạy `gpt-6-luna`: `0,10 USD` / 1M token vào, `0,50 USD` / 1M token ra, `0,01 USD` / 1M token `cache_read`). `SERPER_API_KEY` (tuỳ chọn, `https://serper.dev/api-key`) dùng cho tìm kiếm Google qua `mcp-servers` (~`$0,001`/lần). |
| `env/cong_ai.khoa.env` | `OPENAI_API_KEY` | **Bắt buộc** | Đồng bộ từ `agentic-backend.khoa.env` để Cổng AI giữ khoá gọi `gpt-6-luna`. |
| `env/platform-api-openai.khoa.env` & `env/eval.khoa.env` | `OPENAI_API_KEY` | **Bắt buộc** | Dùng cho `platform-api` (kho bảng SQL) và dịch vụ chấm điểm `eval-service` (`eval.khoa.env` đúng 1 dòng). |
| `env/openrouter.khoa.env` | `OPENROUTER_API_KEY` | **Tuỳ chọn** (khuyến nghị cho khung chat) | Lấy tại `https://openrouter.ai/keys`. Nếu đã có `OPENAI_API_KEY`, khung chat ưu tiên `OPENAI_API_KEY`; điền `OPENROUTER_API_KEY` làm tuyến định tuyến dự phòng. |
| `${CAPITOL_STATE_DIR}/bedrock_046/bedrock-046-creds.json` | JSON STS token / khoá AWS Bedrock | **Tuỳ chọn** | Chỉ cần nếu chuyển cấu hình nóng sang dùng Claude Sonnet 5.5 / Opus 5.5 qua Amazon Bedrock. |
| `env/e2b.khoa.env` | `CHAY_THU_E2B_KHOA` (tuỳ chọn `CHAY_THU_E2B_TEMPLATE`) | Bắt buộc nếu bật E2B | Lấy key tại `https://e2b.dev/dashboard`; `CHAY_THU_E2B_TEMPLATE` mặc định `code-interpreter-v1` nếu bỏ trống. Nếu chưa dùng E2B, sửa các dòng `= e2b` trong `cau_hinh_mo_hinh/noi_chay_khoi.txt` về `may`. |
| `env/exa.khoa.env` | `EXA_API_KEY` | **Tuỳ chọn** | Lấy tại `https://dashboard.exa.ai/api-keys`. Chỉ cần khi bật tra cứu web Exa trên khối **Nghiên cứu sâu** (`capitol-mcp-exa`). |
| `env/qdrant_svc.khoa.env` | `OPENAI_API_KEY` | **Tuỳ chọn** | Chỉ cần nếu tự bật dịch vụ `qdrant_svc` gốc của Capitol (hệ thống mặc định dùng `mekongai-rag`). |

> **Các biến nền gốc Capitol KHÔNG cần đăng ký key (để trống/tắt sẵn trong `agentic-backend.env`, `platform-api.env`, `mcp.env`):** `SERPAPI_KEY`, `DIFFBOT_KEY`, `ANTHROPIC_API_KEY`, `CEREBRAS_API_KEY`, `POSTHOG_API_KEY`, `SENTRY_DSN`, `CORALOGIX_SEND_DATA_KEY`, `LANGFUSE_ENABLED=false` — giữ trống nguyên mẫu, **không cần** tạo tài khoản hay điền key cho các biến này.

- **Đặt trần chi tiêu ngày:** kiểm tra file `${CAPITOL_STATE_DIR}/tran_ngay/$(date +%F).txt` (mặc định **`2.0` USD** — áp cho mọi lời gọi `gpt-6-luna` qua OpenAI API, OpenRouter, E2B và Exa; với giá `gpt-6-luna` `0,10 / 0,50 USD` mỗi 1M token vào/ra và `cache_read 0,01 USD`, mức `2.0 USD` đủ chạy nhiều chục lượt thử nghiệm mỗi ngày; nếu chuyển sang Claude qua Bedrock thì lời gọi Bedrock được ghi sổ theo dõi riêng).
- **Tuỳ chọn dựng template E2B riêng:** nếu chưa có template E2B, xem hướng dẫn tại `docker_build/sandbox-py/e2b_doc/README.md` để đẩy mẫu từ `va_e2b/bac2/dung_template_bac2.py` rồi điền ID mẫu vào `env/e2b.khoa.env`.
- **Kiểm tra an toàn:**
  - Chạy `git status` đảm bảo không có file `.env` / `*.khoa.env` nào bị Git theo dõi.
  - **Không bao giờ** chạy `docker compose config` *thiếu* `--quiet` vì lệnh đó sẽ in khoá bí mật ra màn hình.

---

## Bước 7 — Khởi động toàn bộ nền tảng theo thứ tự có kiểm tra

```bash
cd ~/mekongai/mekongai-agent-platform

# Bật toàn bộ lõi Capitol + Cổng AI + Cổng ngoài + cụm KPI + Cổng RAG (nếu mekongai-rag đã chạy ở :1945)
BAT_KPI=co BAT_RAG=co bash scripts_sach/bat_bo_sach.sh
```

*(Nếu chưa bật `mekongai-rag`, đặt `BAT_RAG=khong` — các tính năng KPI và workflow thường vẫn chạy bình thường, chỉ các tính năng hỏi đáp tài liệu RAG báo chưa bật dịch vụ).*

- **Thấy gì là đúng:** Script lần lượt in:
  - `OK: RAM khả dụng ... MiB`
  - `OK: MinIO đang chạy trên mạng nội bộ s3_noi_bo`
  - `OK: capitol-platform-api sẵn sàng (http://127.0.0.1:80/health)`
  - `OK: capitol-ab-api sẵn sàng (http://127.0.0.1:80/health)`
  - `OK: cong-ngoai đang lắng nghe cổng 18300`
  - `OK: đã khởi tạo tổ chức (...) -> .../org_id.txt` (ở lần bật đầu tiên)
  - `OK: kpi-backend sẵn sàng (http://kpi-backend:8110/api/meta)` và `OK: cụm dịch vụ KPI đã sẵn sàng.`
  - Khi bật RAG: `127.0.0.1:19453` lắng nghe và kết nối tới `127.0.0.1:1945`.
- **Sai thì làm gì:**
  - Báo thiếu RAM: kiểm tra `free -m`, tắt ứng dụng nặng trên Windows hoặc tăng `memory` trong `.wslconfig`.
  - `ab-api` lỗi migrate DB: chờ Postgres khoẻ hẳn rồi chạy lại `bash scripts_sach/bat_bo_sach.sh`.
  - Lỗi cổng đã bị chiếm: kiểm tra `ss -ltn` hoặc tắt phiên cũ bằng `bash scripts_sach/tat_bo_sach.sh`.

---

## Bước 8 — Bật giao diện web (`18400`) và kiểm tra sử dụng đầu–cuối

```bash
cd ~/mekongai/mekongai-agent-platform
CAPITOL_STATE_DIR="${CAPITOL_STATE_DIR:-${HOME}/.local/share/capitol-platform}"
set -a
[ -f env/openrouter.khoa.env ] && . env/openrouter.khoa.env
[ -f "${CAPITOL_STATE_DIR}/rag_cong.env" ] && . "${CAPITOL_STATE_DIR}/rag_cong.env"
[ -f "${CAPITOL_STATE_DIR}/rag_nap.env" ] && . "${CAPITOL_STATE_DIR}/rag_nap.env"
set +a
CAPITOL_STATE_DIR="${CAPITOL_STATE_DIR}" \
CAPITOL_RUNTIME_DIR="${CAPITOL_RUNTIME_DIR:-./runtime}" \
RAG_CONG_GOC="http://127.0.0.1:19453" \
python3 -I giao_dien/may_chu.py
```

### `DỪNG-CẦN-NGƯỜI` (Mốc 4 — Mở trình duyệt & kiểm tra thực tế)
1. Mở trình duyệt tại `http://127.0.0.1:18400`.
2. Lấy mã mở trang từ file `${CAPITOL_STATE_DIR}/mo_trang_18400.txt` để truy cập (nếu bật `CAPITOL_BAT_BUOC_DANG_NHAP=1`, đăng nhập theo 1 trong 3 vai trò `nhan_vien`, `nguoi_duyet`, `quan_tri`, mật khẩu mặc định `capitol-noi-bo-2026` hoặc đặt qua `CAPITOL_MAT_KHAU_MAC_DINH`).
3. Gõ thử một câu KPI tháng 10 vào khung chat (ví dụ: *"Tổng lương kinh doanh cả đội tháng 10 là bao nhiêu?"*) → kiểm tra câu trả lời có nhãn tạm tính và con số `152.360.000 đ`.
4. (Nếu đã bật `mekongai-rag` + cổng RAG `19453`): thử kéo một file PDF/Word mẫu vào khung chat để kiểm tra hỏi đáp tài liệu có dẫn nguồn `[trang N]`/`[đoạn N]`.

### Kiểm tra định kỳ sau bật (nhanh)
```bash
docker ps --filter name=capitol- --format 'table {{.Names}}\t{{.Status}}'
curl -s -o /dev/null -w 'UI 18400: %{http_code}\n' http://127.0.0.1:18400/
ss -ltn | grep -E ':(18400|18401|18402|18403|19453)\b'
```

Khi kết thúc làm việc, tắt an toàn (giữ nguyên database và sổ chi tiêu):
```bash
bash scripts_sach/tat_bo_sach.sh
```
