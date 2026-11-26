# Hướng dẫn sử dụng, vận hành và phát triển tiếp

Tài liệu hướng dẫn cách bật/tắt hệ thống hằng ngày, thao tác trên trang web `http://127.0.0.1:18400`, sử dụng Trợ lý KPI bản `c1b4111a` (hỗ trợ kỳ trống, ghép cột và 7 file dữ liệu tháng 10), vận hành cổng RAG `19453`, và quy trình nhánh Git khi phát triển tính năng mới.

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
- **Đăng nhập & lịch sử bền SQLite:** Hội thoại của từng tài khoản lưu bền trong `${CAPITOL_STATE_DIR}/chat_lich_su.db` theo `(user_id, conv)` và giữ nguyên sau khi tải lại trang hoặc khởi động lại máy chủ. Khi bật `CAPITOL_BAT_BUOC_DANG_NHAP=1`, hệ thống phân quyền 3 vai trò (`nhan_vien`, `nguoi_duyet`, `quan_tri`; chỉ `nguoi_duyet` và `quan_tri` được bấm duyệt trên `/api/approve`).
- **Hỏi KPI nhanh:** Gõ câu hỏi tiếng Việt tự nhiên (ví dụ: *"Tháng 10 NV02 được lương kinh doanh bao nhiêu?"*, *"Tổng lương kinh doanh cả đội tháng 10?"*, *"So sánh NV02 và NV04"*).
  - Bộ định tuyến khung chat tập trung vào các nhóm ý định lời thường không chồng lấn (tra cứu/bảng lương KPI, xuất bảng chính thức, hỏi tài liệu, tính trên bảng tính, hỏi kho, dựng quy trình mới; vẫn giữ đầy đủ 20–30 mẫu workflow ở tab Xưởng). Nếu câu hỏi KPI cá nhân thiếu tên/mã nhân viên, trợ lý hỏi thẳng trực tiếp (`0` nút chọn quy trình) và không để lộ tên quy trình nội bộ.
  - Mọi câu trả lời KPI từ khung chat đều đi qua lớp **AI kiểm số (`vong_4`, chạy mặc định trên `gpt-6-luna`)** (kết hợp lớp kiểm số bằng code chạy ghi nhật ký; có thể đổi công tắc ở `cau_hinh_mo_hinh/kiem_so_kpi.txt`), kèm nút **Tải Excel** nháp ngay dưới câu trả lời và hiển thị nhãn **tạm tính, chưa có người duyệt** (không dừng chờ người duyệt).
- **Hỏi trên tài liệu kéo thẳng vào chat:** Kéo file (`.pdf`, `.docx`, `.xlsx`, `.xlsm`, `.xls`, `.csv`, `.png`, `.jpg`/`.jpeg`, `.html`/`.htm`, `.json`, `.txt`, `.md`/`.markdown`) vào khung chat (file tạm được giữ 24 giờ).
  - PDF/Word có lớp chữ được đọc thẳng kèm dẫn `[trang N]` / `[đoạn N]`.
  - PDF dạng ảnh chụp (không có lớp chữ), ảnh rời, HTML, JSON, bảng tính sẽ đi qua cổng RAG `19453` với 3 năng lực mức 1: tự viết lại câu hỏi nối tiếp theo ngữ cảnh hội thoại bằng `gpt-6-luna` (có ghi sổ chi phí), lấy trọn ngữ cảnh khi hỏi liệt kê/đếm (`whole_file_for_lists`), và hiển thị tối đa `4` ảnh biểu đồ an toàn (`png`/`jpeg`/`webp`/`gif`, cấm `svg+xml`) ngay trong câu trả lời (`include_images`).
- **Tính toán trên bảng tính bất kỳ:** Kéo `.xlsx` hoặc `.csv` vào khung chat và hỏi phép tính (cộng, trung bình, đếm, lọc…); hệ thống chuyển sang khối Chạy code trên E2B để tính trên toàn bộ file. *(Kết quả do code tự tính trên file bất kỳ chưa qua lớp AI kiểm số như KPI).*
- **Nhờ dựng quy trình mới:** Mô tả bằng lời để trợ lý dựng workflow mới (áp cho quy trình chung; quy trình KPI dùng bộ mẫu dựng sẵn).

### 2.2 Khung vẽ workflow (Xưởng)
- Cho phép kéo khối, nối dây một chiều (DAG), bật/tắt tra cứu web **Exa** (`capitol-mcp-exa`) trên khối **Nghiên cứu sâu** (`agentic_deep_research_node`, vẫn chặn trộn vào quy trình KPI), kiểm tra (`Kiểm`), `Lưu` và `Chạy`.
- Xem lại diễn biến khối chạy code/E2B ghi nhận đúng thời lượng thực tế trên dòng thời gian và màn kết quả lượt.
- **Luồng bảng thưởng chính thức:** Chạy quy trình có khối **Người duyệt** từ khung vẽ sẽ tạm dừng để người phụ trách bấm `Duyệt` (xuất file Excel chính thức, không khoá trang tính) hoặc `Từ chối` (dừng lượt). Nếu quá hạn chờ duyệt mà không ai bấm, hệ thống tự đi tiếp nhưng chỉ trả bản nháp có nhãn *"tự duyệt khi quá hạn, chưa có người duyệt"*.

### 2.3 Trang Dữ liệu
- **Phần 1 — Số liệu để tính thưởng KPI theo tháng:** Kéo thả tuần tự đủ 4 loại file của kỳ (`raw` `.xlsx`, `chi_tieu` `.xlsx`, `so_tracking` `.xlsx`, và `trang_chi_tieu` `.html` ≤ 5 MB), xem thẻ trạng thái *"đã nhận, còn thiếu…"* và 3 con số tổng của kỳ, dùng màn **Ghép cột kỳ trống** nếu tên cột khác mẫu (tự động đọc lại trạng thái sau khi ghép), tự cấp mã che cho nhân viên mới (`K1`: `NV15…`, không lộ mã thật) và chặn file sai đội (`422 file_sai_doi`).
- **Phần 2 — Tài liệu để hỏi đáp:** Nạp tài liệu dài hạn vào kho tổ chức qua cổng RAG, ẩn tài liệu không dùng nữa, hoặc bấm **"Hỏi trên tài liệu này"** để mở khung chat hỏi trên đúng tài liệu đó (hoặc hỏi tự do trên toàn bộ kho từ khung chat).

### 2.4 Xem lại lượt chạy
- Xem diễn biến từng khối, thời gian chạy, chi phí ghi nhận từ sổ tiền, công cụ AI đã gọi và tải lại file kết quả của lượt.

---

## 3. Trợ lý KPI bản `c1b4111a` & 7 file đầu vào tháng 10

Kho bàn giao tích hợp sẵn thư mục `kpi_agent/` tự đứng được (bản **`c1b4111a`**) kèm **7 file dữ liệu đầu vào tháng 10/2026** tại `sample_data/thang_10/` (~16,38 MB):
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
  - Đổi mô hình AI của khối: sửa tại chỗ `cau_hinh_mo_hinh/mo_hinh_khoi.txt` (mặc định đang comment `# mac_dinh = ...` để chạy **`gpt-6-luna`**).
  - Đổi nơi chạy code (`e2b` hoặc `may`): sửa tại chỗ `cau_hinh_mo_hinh/noi_chay_khoi.txt` (mặc định đã bật `= e2b` cho toàn bộ các loại khối chạy code, kể cả `skill_code_interpreter_node`, `agent_node` và `pptx_chat_node`).
  - Công tắc lớp kiểm số KPI: sửa tại chỗ `cau_hinh_mo_hinh/kiem_so_kpi.txt` (mặc định đi cùng `gpt-6-luna` là `quyet_dinh = ai`, `luat_code_chan = khong`).

---

## 3b. Hướng dẫn sử dụng `gpt-6-luna` (mặc định) và chuyển đổi mô hình (Luna ↔ Claude Bedrock)

Hệ thống bàn giao chạy **`gpt-6-luna` qua OpenAI API** cho toàn bộ khối quy trình và AI kiểm số (`kiem_so_ai.py` `vong_4` gọi Cổng AI `/v1/responses`), **không đòi hỏi tài khoản AWS Bedrock khi dựng máy mới**, đồng thời cho phép chuyển nóng sang **Claude 5.5 (Sonnet/Opus) qua AWS Bedrock** hoặc lùi ngược lại bất cứ lúc nào mà **không cần tạo lại container**.

### 1. Ba file cấu hình nóng khi chạy `gpt-6-luna` (cấu hình mặc định)

| File cấu hình (`cau_hinh_mo_hinh/`) | Giá trị khi chạy `gpt-6-luna` | Ý nghĩa & lý do đồng bộ |
|---|---|---|
| `mo_hinh_khoi.txt` | Comment dòng `mac_dinh` (`# mac_dinh = claude-sonnet-5-5 / medium`, không để dòng hiệu lực nào) | Khi không có dòng `mac_dinh` hiệu lực, mọi khối Agent / tạo file / phân tích chạy `gpt-6-luna` qua Cổng AI tới OpenAI API. |
| `mo_hinh_operator.txt` | Comment dòng Claude (`# claude-sonnet-5-5`, không để dòng hiệu lực nào) | Trợ lý dựng workflow trên trang chạy bằng `gpt-6-luna` thay vì Claude. |
| `kiem_so_kpi.txt` | `quyet_dinh = ai`<br>`luat_code_chan = khong` | Lớp AI kiểm số (`sua_doi/nodes/agent/kiem_so_ai.py` `vong_4`) chạy trực tiếp trên **`gpt-6-luna`** qua `/v1/responses` của Cổng AI (lớp kiểm bằng code vẫn chạy kèm chỉ ghi nhật ký; có thể đổi về `quyet_dinh = code` nếu muốn tắt AI kiểm số). |

### 2. Lưu ý vận hành khi chạy `gpt-6-luna`
- **Giá tham khảo & trần tiền ngày USD:** `gpt-6-luna` có đơn giá `0,10 USD` / 1M token vào, `0,50 USD` / 1M token ra, và `0,01 USD` / 1M token đọc cache (`cache_read`), đi thẳng qua **trần tiền ngày USD** tại `${CAPITOL_STATE_DIR}/tran_ngay/<YYYY-MM-DD>.txt` (mặc định `2.0 USD`). Khi tổng chi tiêu OpenAI + OpenRouter + E2B + Exa chạm trần ngày, Cổng AI sẽ từ chối (HTTP `429`) cho tới khi quản trị viên nâng trần ngày.
- **Trần token mỗi lượt khối:** Khối Agent thông thường giới hạn `120.000` token vào / `16.000` token ra; hộp phiên đọc tài liệu / tạo file giới hạn `200.000` token vào / `24.000` token ra (chống vòng lặp hoặc ngữ cảnh phình quá mức).
- **Tránh để cuộc trò chuyện quá dài khi không có Bedrock:** Cấu hình nền của `agentic-backend` đang đặt `CONTEXT_COMPACTION_MODEL=claude-opus-4-7` (được định tuyến sang Bedrock trong `cong_ai/dinh_tuyen.yaml`). Nếu cuộc chat kéo quá dài tới ngưỡng phải tóm gọn ngữ cảnh mà máy chưa cấu hình `bedrock-046-creds.json`, bước tóm gọn sẽ lỗi — vì vậy nên bấm **"Cuộc mới"** để mở phiên chat mới sau mỗi vài chục lượt hỏi đáp dài.

### 3. Cách chuyển sang Claude 5.5 qua AWS Bedrock và cách lùi nhanh về `gpt-6-luna`
> **Lưu ý an toàn:** Chỉ sửa 3 file dưới đây **tại chỗ** (lưu đè nội dung, không xoá rồi tạo lại file để tránh mất liên kết bind-mount của container) và chỉ sửa khi **không có lượt workflow nào đang chạy**. Thay đổi có hiệu lực ngay ở lượt kế tiếp, **không cần khởi động lại container**.

- **Chuyển sang Claude Sonnet 5.5 / Opus 5.5 (khi đã đặt `${CAPITOL_STATE_DIR}/bedrock_046/bedrock-046-creds.json`):**
  1. Trong `cau_hinh_mo_hinh/mo_hinh_khoi.txt`: bỏ dấu `#` ở dòng `mac_dinh = claude-sonnet-5-5 / medium` (hoặc chọn `claude-opus-5-5 / medium`).
  2. Trong `cau_hinh_mo_hinh/mo_hinh_operator.txt`: bỏ dấu `#` ở dòng `claude-sonnet-5-5`.
  3. Giữ `cau_hinh_mo_hinh/kiem_so_kpi.txt` ở `quyet_dinh = ai` (AI kiểm số vẫn chạy `gpt-6-luna` ổn định và độc lập).
- **Lùi nhanh về `gpt-6-luna` (0 giây, không cần khởi động lại):**
  1. Đặt `#` trước dòng `mac_dinh = ...` trong `cau_hinh_mo_hinh/mo_hinh_khoi.txt`.
  2. Đặt `#` trước dòng `claude-sonnet-5-5` trong `cau_hinh_mo_hinh/mo_hinh_operator.txt`.
  3. Giữ `quyet_dinh = ai` (hoặc chuyển sang `quyet_dinh = code` nếu muốn dùng bộ luật kiểm số thuần Python).

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
