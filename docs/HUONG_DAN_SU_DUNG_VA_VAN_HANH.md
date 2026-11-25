# Hướng dẫn sử dụng, vận hành và phát triển tiếp

Tài liệu hướng dẫn cách bật/tắt hệ thống hằng ngày, thao tác trên trang web `http://127.0.0.1:18400`, sử dụng Trợ lý KPI bản `252dc30a` (kèm 7 file dữ liệu đầu vào tháng 10), vận hành cổng RAG `19453`, và quy trình nhánh Git khi phát triển tính năng mới.

---

## 1. Bật và tắt hệ thống hằng ngày

### Khi bật lại sau khi máy khởi động lại hoặc sau khi dừng
1. Đảm bảo Docker Desktop đang chạy và dịch vụ `mekongai-rag` (nếu dùng hỏi đáp tài liệu) đã khoẻ ở `http://127.0.0.1:1945/health`.
2. Chạy script khởi động tuần tự có kiểm tra:
   ```bash
   cd ~/mekongai/mekongai-agent-platform
   BAT_KPI=co BAT_RAG=co bash scripts_sach/bat_bo_sach.sh
   ```
   *(Nếu không dùng RAG, đặt `BAT_RAG=khong`.)*
3. Bật máy chủ giao diện `18400` (và các tiến trình nền chạy code cách ly như `sandbox/nguoi_chay_hop.py`, `sandbox/nguoi_chay_phien.py` nếu dùng hộp kín nội bộ):
   ```bash
   CAPITOL_STATE_DIR="${CAPITOL_STATE_DIR:-${HOME}/.local/share/capitol-platform}" \
   CAPITOL_RUNTIME_DIR="${CAPITOL_RUNTIME_DIR:-./runtime}" \
   python3 -I giao_dien/may_chu.py
   ```
4. Mở `http://127.0.0.1:18400`, lấy mã mở trang tại `${CAPITOL_STATE_DIR}/mo_trang_18400.txt` để truy cập.

### Khi tắt hệ thống an toàn (không mất dữ liệu)
1. **Kiểm tra không còn lượt workflow nào đang chạy** (`Running` trên màn hình `18400` hoặc giao diện Temporal `http://127.0.0.1:18233`), vì Temporal lưu bền sẽ tự chạy tiếp các lượt dở khi bật lại.
2. Dừng máy chủ `may_chu.py` (Ctrl+C hoặc dừng tiến trình) rồi chạy:
   ```bash
   bash scripts_sach/tat_bo_sach.sh
   ```
3. **TUYỆT ĐỐI KHÔNG dùng `docker compose down -v`** nếu không muốn xoá sạch toàn bộ volume PostgreSQL (46 bảng, workflow đã lưu, lịch sử chạy) và MinIO.

---

## 2. Sử dụng trang web `http://127.0.0.1:18400`

### 2.1 Khung chat ("Trợ lý")
- **Hỏi KPI nhanh:** Gõ câu hỏi tiếng Việt tự nhiên (ví dụ: *"Tháng 10 NV02 được lương kinh doanh bao nhiêu?"*, *"Tổng lương kinh doanh cả đội tháng 10?"*, *"So sánh NV02 và NV04"*).
  - Bộ định tuyến sẽ tự chọn một trong **23 quy trình đang mở** (trong tổng số 25 thẻ khung chat). Nếu chưa rõ ý, khung chat hiện vài nút gợi ý để bấm chọn.
  - Mọi câu trả lời KPI từ khung chat đều đi qua bước **AI kiểm số** (có đáp án từ công cụ tính) và hiển thị nhãn **tạm tính, chưa có người duyệt** (không dừng chờ người duyệt).
- **Hỏi trên tài liệu kéo thẳng vào chat:** Kéo file (`.pdf`, `.docx`, `.xlsx`, `.xlsm`, `.xls`, `.csv`, `.png`, `.jpg`/`.jpeg`, `.html`/`.htm`, `.json`, `.txt`, `.md`/`.markdown`) vào khung chat (file tạm được giữ 24 giờ).
  - PDF/Word có lớp chữ được đọc thẳng kèm dẫn `[trang N]` / `[đoạn N]`.
  - PDF dạng ảnh chụp (không có lớp chữ), ảnh rời, HTML, JSON, bảng tính sẽ đi qua cổng RAG `19453`.
  - Với PDF có biểu đồ/hình vẽ kéo thẳng: khi câu hỏi nhắc tới hình/biểu đồ, hệ thống đưa file vào dịch vụ RAG để mô tả hình và báo người dùng hỏi lại sau ít phút.
- **Tính toán trên bảng tính bất kỳ:** Kéo `.xlsx` hoặc `.csv` vào khung chat và hỏi phép tính (cộng, trung bình, đếm, lọc…); hệ thống chuyển sang khối Chạy code trên E2B để tính trên toàn bộ file. *(Kết quả do code tự tính trên file bất kỳ chưa qua lớp AI kiểm số như KPI).*
- **Nhờ dựng quy trình mới:** Mô tả bằng lời để trợ lý dựng workflow mới (áp cho quy trình chung; quy trình KPI dùng bộ mẫu dựng sẵn).

### 2.2 Khung vẽ workflow (Xưởng)
- Cho phép kéo khối, nối dây một chiều (DAG), kiểm tra (`Kiểm`), `Lưu` và `Chạy`.
- **Luồng bảng thưởng chính thức:** Chạy quy trình có khối **Người duyệt** từ khung vẽ sẽ tạm dừng để người phụ trách bấm `Duyệt` (xuất file Excel chính thức, không khoá trang tính) hoặc `Từ chối` (dừng lượt). Nếu quá hạn chờ duyệt mà không ai bấm, hệ thống tự đi tiếp nhưng chỉ trả bản nháp có nhãn *"tự duyệt khi quá hạn, chưa có người duyệt"*.

### 2.3 Trang Dữ liệu
- **Phần 1 — Số liệu để tính thưởng KPI theo tháng:** Kéo thả file Excel (`.xlsx`, ≤ 20 MB) gồm **Đơn hàng** (`raw`) và **Chỉ tiêu nhân viên** (`chi_tieu`), chọn tháng/ngày chốt, dùng màn **Ghép cột** nếu tên cột khác mẫu, và xem bảng tình trạng *"Tháng này đã có gì"* lấy trực tiếp từ `kpi_agent`.
- **Phần 2 — Tài liệu để hỏi đáp:** Nạp tài liệu dài hạn vào kho tổ chức qua cổng RAG, ẩn tài liệu không dùng nữa, hoặc bấm **"Hỏi trên tài liệu này"** để mở khung chat hỏi trên đúng tài liệu đó (hoặc hỏi tự do trên toàn bộ kho từ khung chat).

### 2.4 Xem lại lượt chạy
- Xem diễn biến từng khối, thời gian chạy, chi phí ghi nhận từ sổ tiền, công cụ AI đã gọi và tải lại file kết quả của lượt.

---

## 3. Trợ lý KPI bản `252dc30a` & 7 file đầu vào tháng 10

Kho bàn giao tích hợp sẵn thư mục `kpi_agent/` tự đứng được (đóng băng từ commit **`252dc30a`**) kèm **7 file dữ liệu đầu vào tháng 10/2026** tại `sample_data/thang_10/` (~16,38 MB):
- **7 file đầu vào gồm:** file đơn hàng tháng 10, file chỉ tiêu nhân viên, danh sách tuyến cửa hàng, file luật tháng 10, file luật phụ (thưởng thêm, 1% gia vị, hội viên), bảng mã nhân viên (`NV01`–`NV14`), và sổ theo dõi/trang đối chiếu.
- **Số liệu chuẩn tháng 10/2026 đã kiểm chứng:**
  - Tổng lương kinh doanh 8 KPI (14 nhân viên × 8 KPI = 112 ô): **`152.360.000 đ`** (khớp 112/112 ô so với trang theo dõi đội).
  - Thưởng thêm (9 khoản riêng): **`19.026.300 đ`**.
  - Khoản 1 % gia vị trả cho cửa hàng: **`11.868.854 đ`**.
- **Chạy kiểm tra offline `kpi_agent` độc lập (0 đồng, không cần Docker):**
  ```bash
  cd kpi_agent
  uv sync
  # Chạy bộ test cốt lõi (lưu ý test_evaluations.py dùng git log tại gốc repo Git)
  uv run pytest -q
  # Chạy thử workflow tính thưởng tháng 10
  bash scripts/with_env.sh uv run python -m kpi_agent.cli run workflows/kpi_thuong_nvbh_raw.yaml --cutoff 2026-10-31
  ```
- **Thay đổi cấu hình nóng cho hệ thống (không cần tạo lại container):**
  - Đổi mô hình AI của khối: sửa tại chỗ `cau_hinh_mo_hinh/mo_hinh_khoi.txt` (mặc định `claude-sonnet-5-5 / medium`).
  - Đổi nơi chạy code (`e2b` hoặc `may`): sửa tại chỗ `cau_hinh_mo_hinh/noi_chay_khoi.txt`.
  - Công tắc lớp kiểm số KPI: sửa tại chỗ `cau_hinh_mo_hinh/kiem_so_kpi.txt` (`quyet_dinh = ai`, `luat_code_chan = khong`).

---

## 4. Hợp đồng kết nối Cổng RAG (`127.0.0.1:19453` → `mekongai-rag` `127.0.0.1:1945`)

Cổng RAG (`va_rag/rag_cong.py`) chạy trên máy WSL host tại `127.0.0.1:19453` (các container `capitol-ab-*` gọi qua `http://host.docker.internal:19453`, còn `may_chu.py` gọi qua `http://127.0.0.1:19453`). Mọi endpoint khác của `mekongai-rag` (kể cả `/api/v1/qdrant/collections/all` và `/api/v1/chatbot-reference`) đều bị cổng từ chối `404`.

| Cổng `19453` nhận | Chuyển sang `mekongai-rag` (`:1945`) | Bảo mật & Giới hạn tại cổng |
|---|---|---|
| `POST /api/v1/search` | `POST /api/v1/search` | Header `X-Rag-Ma` + `X-Rag-Org`; tự suy `user_id = "capitol_" + sha256(org)[:32]`, ép collection `CHATBOT_<user_id>_*`; `≤ 20 lần/60s`, timeout `35s`, trả về `≤ 4 MB`. |
| `GET /api/v1/kho` | `GET /api/v1/qdrant/collections/user/{user_id}` | Header `X-Rag-Ma` + `X-Rag-Org`; tự suy `{user_id}`, `≤ 20 lần/60s`, timeout `35s`. |
| `POST /api/v1/nap` | `POST /api/v1/upload-files` | Header `X-Rag-Nap-Ma` (chỉ `may_chu.py` giữ) + `X-Rag-Org` + `X-Ten-File` + `X-Nap-Lai`; multipart 4 trường (`user_id`, `language="vie+eng"`, `force_reindex`, `file` — **không bao giờ gửi `url`**); `1 B – 20 MB`, `≤ 30 lần/giờ`, `≤ 100 trang/file`, `≤ 400 trang/ngày`, `MemAvailable ≥ 4096 MB`, timeout `900s`. |

- **Phụ thuộc bắt buộc:** `pdfinfo` và `pdftotext` (`poppler-utils`) dùng để đếm trang độc lập 2 lớp và trích chữ PDF.

---

## 5. Vận hành chi phí, khoá tạm & phát triển tính năng mới bằng Git

### Theo dõi chi phí & làm mới khoá Bedrock
- **Khoá Amazon Bedrock:** Nếu dùng STS token tạm (~6 giờ hết hạn), cập nhật file JSON tại `${CAPITOL_STATE_DIR}/bedrock_046/bedrock-046-creds.json` (hoặc script làm mới khoá nội bộ của công ty); `capitol-cong-ai` tự đọc lại ở lượt kế tiếp, không cần khởi động lại container.
- **Trần ngày & sổ tiền:** Mọi lượt chạy qua `18400` đều ghi sổ tại `${CAPITOL_RUNTIME_DIR}/`; lời gọi Bedrock ghi sổ chi tiết theo token tại `${CAPITOL_RUNTIME_DIR}/so_cong/`, còn các dịch vụ trả theo lượt (OpenRouter, E2B, Exa) bị chặn theo ngưỡng USD tại `${CAPITOL_STATE_DIR}/tran_ngay/<YYYY-MM-DD>.txt`.

### Quy trình Git khi phát triển tính năng mới
1. Không làm việc trực tiếp trên `main`. Tạo nhánh mới cho mỗi tính năng hoặc bản sửa lỗi:
   ```bash
   git checkout -b feat/<ten-tinh-nang>
   ```
2. Kiểm tra trước khi gộp:
   - Cú pháp Python: `python3 -I -m py_compile <file.py>`
   - Giao diện web (nếu có sửa `giao_dien/web/src/`): `cd giao_dien/web && npm run kiem-kieu && npm run build`
   - Đảm bảo không gắn cứng đường dẫn cá nhân, không commit `.env` / `*.khoa.env`, không commit dữ liệu nhân sự cá nhân (tên + thu nhập thật).
3. Cập nhật tài liệu trong `docs/` nếu thay đổi biến môi trường, cổng mạng hoặc quy trình cài đặt, sau đó tạo Pull Request / merge vào `main`.
