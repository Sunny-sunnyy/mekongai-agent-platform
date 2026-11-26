# Trợ lý KPI thưởng NVBH (`kpi_agent`) — Bản 1.1

Hệ thống tính thưởng KPI, đối soát dữ liệu đơn hàng và hỏi đáp số liệu cho giám sát bán hàng, đóng gói tự đứng để chạy độc lập hoặc tích hợp vào kho `mekongai-agent-platform` (`https://github.com/hieumekongai/mekongai-agent-platform`).

## Kiến trúc 3 tầng

Mặc định chỉ nghe trên `127.0.0.1` và yêu cầu mã phiên (`X-Kpi-Session`) ở mọi lời gọi:

1. **Backend chỉ đọc (`:8110`)** — `kpi_agent.backend.main:app`:
   - Chạy workflow tính 8 KPI (`kpi_agent/core/` + `kpi_agent/workflow/`), thưởng thêm, 1 % gia vị, hội viên, kiểm tham số đầu vào và quản lý bản tính đã soát trong SQLite.
   - Hỗ trợ khởi tạo **kỳ trống chỉ bằng tải lên** (`KPI_EMPTY_PERIOD=<YYYY-MM>`), ghép cột lệch mẫu ngay khi kỳ chưa có bản tính, tự cấp mã che `NV15…` cho nhân viên mới khi tải file chỉ tiêu (`K1`), và ghi nhãn thời gian sự kiện theo múi giờ Việt Nam (`UTC+7`).
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

# Khởi động ở chế độ kỳ trống (chờ người dùng tải đủ đơn hàng + chỉ tiêu + sổ theo dõi)
KPI_EMPTY_PERIOD=2026-10 bash kpi_agent/scripts/chay_ban1.sh start
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

## Điểm mới & giới hạn (Bản 1.1)

- **Mốc đóng gói:** Bản 1.1 xuất từ commit `c1b4111a`, giữ nguyên lõi tính 8 KPI tháng 10 (`152.360.000 · 19.026.300 · 11.868.854 đ`, `table_sha256` ổn định) và bổ sung:
  - **Kỳ trống chỉ tải lên:** mọi mục `raw`, `chi_tieu`, `tuyen` dựng từ kỳ trống hiển thị trạng thái đã tải lên (`from_upload=true`, `checked=true`, `uploaded_at` giờ `UTC+7`), đồng thời trả lời rõ bằng tiếng Việt khi thiếu cột doanh số sau chiết khấu cho khoản 1 % gia vị.
  - **Ghép cột tại kỳ trống:** các đường `/api/system/khuon/{table}`, `/api/system/bo-cuc`, `/api/system/ghep-thu`, `/api/system/ghep-xac-nhan` chạy được ngay khi kỳ chưa có bản tính, cho phép ghép file đơn hàng và sổ theo dõi lệch mẫu từ bước đầu.
  - **Nhân viên mới (`K1`):** tự động cấp mã che nối tiếp (`NV15…`) và lưu bền trong bảng chỉ-thêm `auto_employee_mask` (SQLite kho tải lên) khi tải file `chi_tieu`; file `raw` lạ vẫn bị chặn, file `chi_tieu` sai đội bị chặn với mã `422 file_sai_doi`, và tải lại file 14 người sau khi đã cấp `NV15` vẫn hợp lệ.
  - **Phản hồi tuyến đối chiếu lại:** mọi lần tải lên bản lấy tuyến từ sổ đều kèm ô `tuyen_tu_so.tom_tat` phản ánh tỷ lệ đối chiếu trên chính file đơn hàng của bản đó.
- **Giới hạn đã biết:**
  - Chế độ "một nguồn số" qua cổng xem (`KPI_XEM_URL`) chỉ cho phép đọc số và hỏi đáp, chưa hỗ trợ tải file hay duyệt bản tính trực tiếp từ cổng xem.
  - Bản dry-run đã qua kiểm tĩnh (`compileall`), rà sạch dấu vết nội bộ và kiểm thử Python cốt lõi (`pytest`); bước dựng lại container thực tế trên máy đích cần kiểm lại `/health` và giao diện web khi triển khai.
