# Phiếu kiểm nhận bàn giao trên máy trống (cho đồng nghiệp)

> **Mục đích:** Giúp người nhận kiểm tra từng bước khi dựng lại hệ thống trên máy mới (mặc định **Windows + WSL2 Ubuntu + Docker Desktop**, RAM khuyến nghị **≥ 16 GB**; Linux/macOS thực hiện tương tự ở phần lệnh shell).
> **Phạm vi bản 1:** Bàn giao đóng gói tĩnh (mã nguồn sạch, cấu hình mẫu, bộ dữ liệu mẫu tháng 10, `Dockerfile` + lockfile, bảng kê `.tar` trên Google Drive). Các bước chạy Docker trên máy nhận cần tự điền API key vào `.env` trước khi khởi động.

---

## 1. Kiểm tra điều kiện máy trước khi bắt đầu

- [ ] **Hệ điều hành & Docker:**
  - Windows + WSL2 (Ubuntu) + Docker Desktop đang bật (WSL2 backend) hoặc Linux/macOS với Docker Engine + Docker Compose v2.
  - Kiểm tra lệnh: `docker --version` và `docker compose version` đều chạy không lỗi.
- [ ] **RAM & dung lượng ổ đĩa:**
  - RAM cấp cho WSL2 / Docker **≥ 16 GB** (`free -h` trong WSL báo `Mem` ≥ 15 GiB).
  - Ổ đĩa trống tối thiểu **35 GB** (nạp 7 `.tar` ~7–10 GB + build 4 image tự dựng + dữ liệu Postgres/MinIO/Qdrant).
- [ ] **Công cụ phụ trợ trên WSL / host:**
  - `git`, `python3` (`3.12`), `uv`, `node` (`>= 20`) + `npm`, `curl`, `poppler-utils` (`pdfinfo`, phục vụ kiểm trang PDF đầu vào RAG).
  - Kiểm tra cổng trống trước khi bật: `1945`, `6333`, `8110`, `8120`, `5183`, `18400`–`18403`, `19453`.

---

## 2. Kiểm tra đầy đủ hai kho GitHub & Google Drive

- [ ] **Kho 1 (`mekongai-rag`):** đã clone từ `https://github.com/hieumekongai/mekongai-rag`
  - Có đủ: mã nguồn RAG, `compose.yaml`, `Dockerfile`, `pyproject.toml`, `uv.lock`, `.env.example`, `.env.qdrant.example`, dữ liệu mẫu nhẹ (`sample_data/`), tài liệu hướng dẫn cài & sử dụng.
  - Đảm bảo **không có** file `.env` thật hay khoá sẵn trong lịch sử git.
- [ ] **Kho 2 (`mekongai-agent-platform`):** đã clone từ `https://github.com/hieumekongai/mekongai-agent-platform`
  - Có đủ: mã nguồn nền tảng điều phối/giao diện, cấu hình compose sạch, các `.env.example`, `Dockerfile` + lockfile cho các service tự dựng (`kpi-backend`, `sandbox-py`, `mcp-exa`), thư mục `kpi_agent/` tự đứng (bản đóng băng `252dc30a`) kèm 7 file dữ liệu đầu vào tháng 10 (`sample_data/thang_10/`), và bộ tài liệu hướng dẫn trong `docs/`.
- [ ] **Google Drive công ty (7 file `.tar` gốc Capitol, ~6,57 GiB):**
  - Đã tải đủ 7 file `.tar` + `SHA256SUMS` vào cùng thư mục.
  - Chạy kiểm checksum tại thư mục chứa file:
    ```bash
    sha256sum -c SHA256SUMS
    ```
  - **Đạt khi:** tất cả **7 / 7 dòng** in `: OK` (`agentic-backend`, `platform-api`, `mcp-servers`, `mcp-fastapi`, `artifact-blob-store`, `eval-service`, `qdrant-svc`).
  - Nạp vào Docker cục bộ:
    ```bash
    for f in *.tar; do docker load -i "$f"; done
    docker images | grep capitol-local
    ```
  - **Đạt khi:** thấy đủ 7 image `capitol-local/*`.

---

## 3. Tự điền `.env` (TUYỆT ĐỐI không commit ngược lên Git)

- [ ] **Ở `mekongai-rag`:** sao chép `.env.example` → `.env` và `.env.qdrant.example` → `.env.qdrant`, điền API key mô hình (OpenAI / AWS Bedrock / Anthropic tuỳ cấu hình) và khoá nội bộ Qdrant.
- [ ] **Ở `mekongai-agent-platform`:** sao chép toàn bộ các file `.env.example` sang `.env` / `*.khoa.env` đúng vị trí theo hướng dẫn cài đặt; điền đủ khoá dịch vụ cần dùng:
  - LLM mặc định (`gpt-6-luna`): **`OPENAI_API_KEY` bắt buộc** (trong `env/agentic-backend.khoa.env`, `env/cong_ai.khoa.env`, `env/platform-api-openai.khoa.env`, `env/eval.khoa.env`)
  - Bộ định tuyến khung chat: `OPENROUTER_API_KEY` (trong `env/openrouter.khoa.env`)
  - Amazon Bedrock (tuỳ chọn, nếu chuyển sang Claude 5.5): đặt credentials JSON vào `${CAPITOL_STATE_DIR}/bedrock_046/bedrock-046-creds.json`
  - Sandbox chạy code: `E2B_API_KEY` / `CHAY_THU_E2B_KHOA` (nếu bật tính năng chạy code cách ly trên E2B; hoặc để `may` trong `cau_hinh_mo_hinh/noi_chay_khoi.txt`)
  - Tìm kiếm web (tuỳ chọn): `EXA_API_KEY`
  - Khoá nội bộ tự sinh giữa các dịch vụ (Postgres, MinIO, Redis, mã phiên mở trang, mã dịch vụ nội bộ — tự sinh qua `scripts_sach/khoi_tao_lan_dau.sh`).
- [ ] Kiểm tra 3 file cấu hình nóng mặc định cho `gpt-6-luna`:
  - `cau_hinh_mo_hinh/mo_hinh_khoi.txt` (dòng `mac_dinh = claude-...` đã comment `#`)
  - `cau_hinh_mo_hinh/mo_hinh_operator.txt` (dòng `claude-...` đã comment `#`)
  - `cau_hinh_mo_hinh/kiem_so_kpi.txt` (`quyet_dinh = code`)
- [ ] Kiểm tra `git status` ở cả 2 kho đều không có file bí mật nào bị theo dõi (`.gitignore` đã chặn `.env` / `*.khoa.env`).

---

## 4. Dựng & kiểm tra theo đúng thứ tự phụ thuộc (`RAG → KPI + Nền tảng`)

### Bước A — Bật `mekongai-rag` (`:1945`)
- [ ] Chạy dựng và khởi động tại thư mục `mekongai-rag`:
  ```bash
  docker compose up -d --build
  ```
- [ ] Kiểm tra sức khoẻ dịch vụ:
  - `curl -s http://127.0.0.1:1945/health` trả HTTP `200`.
  - Qdrant (`127.0.0.1:6333`) ở trạng thái `healthy`.
  - `GET /api/v1/active-config` trả JSON cấu hình hợp lệ.
- [ ] Thử nạp 1 file mẫu trong `sample_data/` qua `POST /api/v1/upload-files` và hỏi đáp qua `POST /api/v1/search`.

### Bước B — Bật `kpi_agent` độc lập (`:8110`, `:8120`, `:5183` — tuỳ chọn kiểm nhanh)
- [ ] Tại thư mục `kpi_agent/`:
  ```bash
  uv sync
  python -m pytest -q
  ```
  **Đạt khi:** bộ test Python offline chạy qua không lỗi.

### Bước C — Dựng & bật nền tảng chính (`mekongai-agent-platform`)
- [ ] Chạy chuỗi khởi tạo lần đầu và dựng các image tự build (`kpi-backend`, `sandbox-py`, `mcp-exa`) theo hướng dẫn cài đặt trong `docs/`.
- [ ] Khởi động cụm Docker Compose của nền tảng và tiến trình giao diện / cổng RAG (`:19453`, nối sang `mekongai-rag` `:1945`).
- [ ] Kiểm tra các điểm chạm cốt lõi:
  - Toàn bộ container đều ở trạng thái `Up` / `healthy`.
  - Trang giao diện web (`http://127.0.0.1:18400`) trả HTTP `200`, đăng nhập / mở trang bằng mã phiên thành công.
  - Thử 1 câu hỏi KPI tháng 10 trên khung chat → trả số khớp bảng tính (`152.360.000 đ` tổng lương KPI nhóm 14 NV theo dữ liệu mẫu tháng 10).
  - Thử kéo 1 file mẫu vào khung chat để hỏi qua cổng RAG → trả lời có dẫn nguồn.

---

## 5. Lưu ý giới hạn đã biết (không phải lỗi cài đặt)

- **Bản lõi KPI đóng băng:** Kho bàn giao dùng bản **`252dc30a`** (đã kiểm 112 ô KPI và lương 14 nhân viên trong hệ thống). Nhánh phát triển có bản mới hơn hỗ trợ khởi tạo kỳ trống, hiện chưa nối vào giao diện bản 1.
- **Trạng thái diễn tập máy trống:** Bản xuất kho bàn giao bản 1 đã được kiểm tĩnh toàn bộ (an toàn bí mật = 0, không mang harness/đường dẫn máy cục bộ, kiểm cú pháp Python và liên kết tài liệu), chưa diễn tập dựng lại Docker đầu–cuối từ máy trống trong đợt đóng gói này.
- **Dữ liệu nguồn R2 (~97 GB):** Không đóng gói vào GitHub hay Google Drive; khi cần dữ liệu gốc bổ sung, lấy trực tiếp từ Cloudflare R2 (bucket `capitol2`, thông tin xác thực trao đổi riêng qua kênh nội bộ công ty).
