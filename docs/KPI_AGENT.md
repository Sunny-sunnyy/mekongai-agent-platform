# Trợ lý KPI thưởng NVBH (`kpi_agent`)

Hệ thống tính thưởng KPI, đối soát dữ liệu đơn hàng và hỏi đáp số liệu cho giám sát bán hàng, đóng gói tự đứng để chạy độc lập hoặc tích hợp vào kho `mekongai-agent-platform` (`https://github.com/hieumekongai/mekongai-agent-platform`).

## Kiến trúc 3 tầng

Mặc định chỉ nghe trên `127.0.0.1` và yêu cầu mã phiên (`X-Kpi-Session`) ở mọi lời gọi:

1. **Backend chỉ đọc (`:8110`)** — `kpi_agent.backend.main:app`:
   - Chạy workflow tính 8 KPI (`kpi_agent/core/` + `kpi_agent/workflow/`), thưởng thêm, 1 % gia vị, hội viên, kiểm tham số đầu vào và quản lý bản tính đã soát trong SQLite.
   - Mặc định dùng workflow đường RAW DATA (`kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml`, ngày chốt `2026-10-31`).
2. **Agent hỏi đáp (`:8120`)** — `kpi_agent.agent.server:app`:
   - Nhận `POST /api/chat`, giữ lịch sử hội thoại trên server, gọi công cụ đọc số từ backend và kiểm số trước khi trả lời (`kpi_agent/agent/kiem_so.py`).
   - Hỗ trợ chế độ "một nguồn số" qua cổng xem của nền tảng Capitol khi đặt `KPI_XEM_URL`.
3. **Giao diện web (`:5183`)** — `kpi_agent/dashboard/` (React + Vite):
   - Bảng tổng quan cả đội, thẻ chi tiết nhân viên, đối chiếu trang theo dõi, chỉnh/chạy thử workflow (`/keo_tha.html`) và khung chat trợ lý.

## Yêu cầu môi trường

- Hệ điều hành khuyến nghị: **Windows + WSL Ubuntu** (≥ 16 GB RAM) hoặc Linux / macOS.
- Python `3.12` + `uv`
- Node.js `>= 20` + `npm` (cho giao diện `kpi_agent/dashboard/`)

## Cài đặt & chạy nhanh (độc lập)

```bash
# 1. Cài dependencies Python
uv sync

# 2. Cài dependencies giao diện web
npm --prefix kpi_agent/dashboard ci

# 3. Tạo file .env ở thư mục gốc (không commit lên Git)
cat > .env <<'EOF'
OPENAI_API_KEY=<your-api-key>
OPENAI_BASE_URL=https://api.openai.com/v1
EOF

# 4. Bật cả 3 dịch vụ nền (:8110, :8120, :5183)
bash kpi_agent/scripts/chay_ban1.sh start

# Xem trạng thái / lấy lại link có mã phiên / tắt dịch vụ
bash kpi_agent/scripts/chay_ban1.sh status
bash kpi_agent/scripts/chay_ban1.sh link
bash kpi_agent/scripts/chay_ban1.sh stop
```

### Đổi cổng hoặc chọn workflow khác

```bash
# Chạy song song trên bộ cổng khác
KPI_BACKEND_PORT=8111 KPI_AGENT_PORT=8121 KPI_WEB_PORT=5184 bash kpi_agent/scripts/chay_ban1.sh start

# Chạy theo sổ Tracking thay cho RAW DATA
KPI_WORKFLOW=kpi_agent/workflows/kpi_thuong_nvbh.yaml KPI_CUTOFF=2026-10-20 bash kpi_agent/scripts/chay_ban1.sh start
```

## Dữ liệu mẫu tháng 10 (`sample_data/thang_10/`)

Thư mục `sample_data/thang_10/` chứa sẵn bộ 7 file đầu vào tháng 10/2026 để chạy và kiểm chứng ngay:

- `Chi tieu NVBH.xlsx` — chỉ tiêu 14 nhân viên × 8 KPI
- `RAW DATA.xlsx` — dữ liệu đơn hàng chi tiết tới 31/10/2026
- `mcp_trang_sep_3110.json` — danh sách điểm bán theo tuyến
- `Tracking.xlsx`, `Tracking.xlsb` — sổ theo dõi tổng hợp
- `Tracking KPI Lam Phương.html` — trang theo dõi xuất từ hệ thống đội
- `Số 21_[2026-10]_2.CONG VAN XET KPI NVBH (CDGT) 01102026 (chinh sach thuong DDKD 10.2026) (1).pdf` — công văn chính sách thưởng

## Chế độ "một nguồn số" với Capitol (`KPI_XEM_URL`)

Khi nối agent KPI trực tiếp vào cổng xem chỉ đọc của Capitol (không bật backend riêng trên máy):

- `KPI_XEM_URL`: URL cổng xem (ví dụ `http://127.0.0.1:18403`)
- `KPI_XEM_MA_FILE`: đường dẫn file quyền `600` chứa `XEM_MA=<token>` (mặc định `${CAPITOL_STATE_DIR}/xem_18403.env`)
- `KPI_VAN_TAY_URL`: URL cổng vân tay hộp chính (mặc định `http://127.0.0.1:18402`)
- `KPI_VAN_TAY_FILE`: đường dẫn file quyền `600` chứa `VAN_TAY_MA=<token>` (mặc định `${CAPITOL_STATE_DIR}/van_tay_18402.env`)
- `KPI_ALLOWED_ORIGINS`: danh sách origin được phép gọi `POST /api/chat` (mặc định `http://127.0.0.1:5183,http://localhost:5183`)

## Kiểm tra nhanh (offline, không gọi LLM)

```bash
.venv/bin/python -m pytest -q
```

## Giới hạn bản 1

- **Mốc đóng băng:** Bản 1 đóng băng tại commit `252dc30a` (đã kiểm 112 ô KPI và lương 14 nhân viên trong hộp Capitol); nhánh phát triển có bản mới hơn (lõi kỳ trống chưa nối giao diện).
- **Trạng thái kiểm chứng:** Bản xuất đã được kiểm tĩnh (`py_compile`, cấu hình YAML, bộ test Python cốt lõi), chưa diễn tập `docker compose up --build` từ máy trống trong đợt đóng gói này.
- Chưa có màn hình khởi tạo **kỳ trống** (tải dữ liệu mới cho kỳ chưa có file sẵn) trên giao diện Capitol.
- Chế độ "một nguồn số" qua cổng xem (`KPI_XEM_URL`) chỉ cho phép đọc số và hỏi đáp, chưa hỗ trợ tải file hay duyệt bản tính trực tiếp từ cổng xem.
