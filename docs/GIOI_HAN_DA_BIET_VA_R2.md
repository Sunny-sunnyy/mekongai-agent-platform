# Giới hạn đã biết (Bản 1) & Nguồn dữ liệu thô Cloudflare R2

Tài liệu này ghi minh bạch tình trạng thực tế của **Bản bàn giao 1**, những phần đang làm dở hoặc chưa kiểm tra thực tế khi đóng gói, và hướng dẫn nguồn gốc kho dữ liệu tham khảo từ Cloudflare R2.

---

## 1. Trạng thái đóng gói (Minh bạch kiểm chứng)

1. **Cập nhật `kpi_agent/` lên bản `c1b4111a`:**
   - Kho bàn giao sử dụng commit **`c1b4111a`** của `kpi_agent`, hỗ trợ đầy đủ chế độ **kỳ trống** (`KPI_EMPTY_PERIOD`), nạp 4 loại file (`raw`, `chi_tieu`, `so_tracking`, `trang_chi_tieu` `.html`), **Ghép cột kỳ trống** qua giao diện `18400`, **tự cấp mã che cho nhân viên mới (`K1`: `NV15…`)** và rào `422 file_sai_doi` — đồng thời giữ nguyên toàn bộ 112 ô (14 nhân viên × 8 KPI = `152.360.000 đ`), `19.026.300 đ` thưởng thêm và `11.868.854 đ` 1% gia vị.
2. **Mức kiểm chứng của bản xuất GitHub:**
   - Phần nền tảng điều phối, giao diện `18400`, AI kiểm số `vong_4` trên `gpt-6-luna`, máy ảo E2B bậc 3, đăng nhập 3 vai trò + lịch sử SQLite, bộ định tuyến khung chat rút gọn và tích hợp RAG mức 1 đã được kiểm tra tích hợp E2E trên hệ thống chạy thật; gói đóng xuất được kiểm tra tĩnh (cú pháp `py_compile`, `tsc --noEmit`, quét khoá/bí mật = 0, quét đường dẫn máy cá nhân = 0).
   - **Đã diễn tập dựng lại cô lập trên DB/thư mục trống (26/11/2026):** xây dựng thành công từ `Dockerfile` (`kpi-backend:c1b4111a`, `mcp-exa`), chạy `scripts_sach/khoi_tao_lan_dau.sh` sinh đủ 16 file cấu hình + 10 file mã nội bộ, chạy `scripts_sach/bat_bo_sach.sh` khởi tạo Alembic Postgres trống + DynamoDB + tổ chức mặc định `org_id.txt` + 17 container (`ab-api`, `platform-api` `/health = 200`, `:18300` lắng nghe) + `capitol-kpi-backend` kỳ trống `2026-10` (`/api/meta`, `/api/tools = 200`) + `capitol-kpi-tai-len` (`:18402/ky/2026-10 = 200`), và giao diện `may_chu.py` (`18400/`, `POST /api/mo`, `/api/mo-hinh-khoi`, `/api/kpi/dung-san` = `200`, `0` traceback).
   - *Lưu ý:* diễn tập dùng sẵn 7 image `capitol-local/*` đã nạp Docker từ gói Drive và giữ `BAT_RAG=khong` lúc diễn tập dựng sạch (phần `:19453 -> :1945` đã kiểm E2E riêng ở Chặng 3B-RAG). Đồng nghiệp dựng trên máy mới hoàn toàn vẫn nên kiểm tra bước `docker load` 7 file `.tar` theo phiếu kiểm nhận.
3. **Phạm vi loại trừ:**
   - Không bao gồm các module thử nghiệm/dự án khác (`sales_agent/`, `enhanced_system/`, `mekongai-social`) và bộ ghi chép đọc hiểu phân tích mã nguồn gốc.

---

## 2. Các giới hạn đã biết của hệ thống hiện tại

### 2.1 Bảo mật, đăng nhập & vận hành nhiều người
- **Đăng nhập cục bộ 3 vai trò (tối giản nội bộ):** Trang `18400` hỗ trợ 3 tài khoản mặc định theo vai trò (`nhan_vien`, `nguoi_duyet`, `quan_tri` — mật khẩu băm `scrypt`, lịch sử chat cô lập theo tài khoản trong SQLite, vai trò `nhan_vien` bị chặn `403` khi bấm Duyệt), đồng thời giữ tương thích ngược đường mở trang `#k=` (`mo_trang_18400.txt`) khi `CAPITOL_BAT_BUOC_DANG_NHAP` chưa bật (`!= "1"`). Khi triển khai vận hành rộng cần bật `CAPITOL_BAT_BUOC_DANG_NHAP=1` và đặt mật khẩu riêng qua `CAPITOL_MAT_KHAU_MAC_DINH`.
- **Chưa có lịch sao lưu tự động:** Dữ liệu PostgreSQL, MinIO, sổ tiền, `chat_lich_su.db` và kho tài liệu nằm trên ổ đĩa/volume máy chạy; cần sao lưu thủ công định kỳ.
- **Đồng hồ WSL có thể lệch nhẹ:** Khi đồng hồ máy WSL dịch chuyển vài giây, hiệu thời gian giữa các mốc trên màn xem lượt có thể chênh lệch nhẹ (giao diện đã bổ sung mốc dự phòng để không báo `0,0 giây` giả cho khối chạy code).

### 2.2 Trợ lý KPI & Trang Dữ liệu
- **Dữ liệu nền mặc định & khởi động lại hộp KPI:** Các file cấu hình luật tháng/phụ và danh sách tuyến có thể nạp kèm lúc bật hộp `capitol-kpi-backend` hoặc tải qua chế độ kỳ trống; nếu khởi động lại hộp mà không gắn volume/thư mục lưu trữ `KPI_KHO` bền vững thì cần nạp lại file kỳ đang mở.
- **Tháng 11/2026 trở đi chưa chạy được:** Cần soạn thêm file luật từ công văn mới, file chỉ tiêu và danh sách tuyến tương ứng.
- **Hai khoản phụ (`19.026.300 đ` thưởng thêm và `11.868.854 đ` 1% gia vị):** Đã được `kpi_agent` tính nhưng chưa có nguồn đối chiếu độc lập ngoài hệ thống. Khoản **Hội viên** và **Thưởng vượt doanh số** chưa tính ra tiền ở tháng 10.
- **Khối Người duyệt & duyệt sau:** Khung chat trả ngay kết quả nháp (không chờ duyệt). Trên khung vẽ, nếu lượt có khối Người duyệt quá hạn không ai bấm, hệ thống tự đi tiếp ra bản nháp có nhãn và **chưa có tính năng "duyệt sau"** cho lượt đã quá hạn.

### 2.3 Lớp kiểm số, mô hình mặc định (`gpt-6-luna`), khung chat & RAG
- **Lớp AI kiểm số mặc định `gpt-6-luna` (`vong_4`, `quyet_dinh = ai`):** Lớp AI kiểm số (`sua_doi/nodes/agent/kiem_so_ai.py`) đã chuyển sang chạy **`gpt-6-luna`** qua Cổng AI (không cần Bedrock); bộ luật bằng code vẫn chạy kèm để đối chiếu nhật ký. Lưu ý lớp kiểm số chủ yếu kiểm **tính khớp con số** (chưa kiểm nghĩa toàn diện ở mọi tình huống) và đôi lúc có thể chặn thận trọng với câu diễn giải phức tạp.
- **Tóm gọn ngữ cảnh hội thoại dài khi chạy Luna không có Bedrock:** Biến `CONTEXT_COMPACTION_MODEL=claude-opus-4-7` đang định tuyến sang Bedrock trong `cong_ai/dinh_tuyen.yaml`; nếu hội thoại chat quá dài chạm ngưỡng tóm gọn mà máy không dùng Bedrock, hệ thống sẽ báo lỗi bước tóm gọn (cách xử lý: bấm "Cuộc mới" trước khi phiên quá dài).
- **Bộ định tuyến khung chat:** Đã rút gọn về các ý định lời thường không chồng lấn; một số nhãn mô tả khối trên giao diện khung vẽ vẫn giữ tên gốc trong `khoi_dung_san.json`.
- **Tính toán trên bảng tính bất kỳ từ khung chat:** Code AI viết chạy trên E2B và tính trên toàn bộ file `.xlsx`/`.csv`, nhưng con số kết quả **chưa đi qua lớp kiểm số** như luồng KPI.
- **Hỏi đáp tài liệu & đọc hình (RAG mức 1):**
  - Đã hỗ trợ viết lại câu hỏi nối tiếp bằng `gpt-6-luna`, lấy trọn danh sách (`whole_file_for_lists`), và hiển thị tối đa `4` ảnh biểu đồ an toàn (`include_images`, cấm SVG).
  - Chưa hỗ trợ định dạng cũ `.doc`, `.ppt`, `.pptx`, `.xlsb`, `.ods` (hệ thống từ chối kèm hướng dẫn lưu lại sang `.docx`/`.pdf`/`.xlsx`); định dạng `.xls` cũ chưa được kiểm thử đủ rộng.
  - Khi hỏi tự do thông thường trên toàn bộ kho, AI đọc tối đa 12 đoạn liên quan nhất.

### 2.4 Chạy code trên E2B & tài khoản ngoài
- **Máy ảo E2B có đường ra Internet** theo cấu hình hiện tại (khi cho phép chạy với file thật); rào bảo vệ chính là lọc đuôi file, giới hạn ≤ 20 MB, dò chuỗi giống khoá trước khi gửi lên máy ảo, không đưa API key máy chủ vào máy ảo, và máy ảo tự huỷ sau 180 giây.
- **Chưa kiểm thử lượt thực tế:** PowerPoint dùng file mẫu riêng của người dùng, lượt chạy E2B dài quá 180 giây, và khối Nghiên cứu sâu tra web Exa trực tiếp qua mạng ngoài trên trang.
- **Khoá Amazon Bedrock tạm thời (tuỳ chọn):** Chỉ liên quan nếu chuyển cấu hình sang Claude 5.5 qua Bedrock; nếu dùng STS token tạm (~6 giờ), quản trị viên cần làm mới định kỳ trước khi hết hạn.

---

## 3. Nguồn gốc dữ liệu thô tham khảo (Cloudflare R2 — bucket `capitol2`)

Trong quá trình nghiên cứu nền tảng Capitol AI, đội ngũ có sử dụng một bộ dữ liệu thô quy mô lớn (~97 GB, ~382 nghìn file) lưu trữ trên **Cloudflare R2** tại bucket **`capitol2`** (gồm các cấu hình workflow mẫu thực tế, nhật ký lượt chạy, bản sao lưu dữ liệu và tài liệu thử nghiệm của Capitol gốc).

- **Chính sách đóng gói Bản 1:** Bộ dữ liệu ~97 GB này **KHÔNG đưa vào GitHub và KHÔNG nén đẩy lên Google Drive** vì dung lượng quá lớn và chứa nhiều file hệ thống/nhật ký cũ của nền tảng gốc (đặc biệt thư mục `capitol2/secrets/` tuyệt đối không sao chép hoặc phát tán).
- **Khi cần lấy lại dữ liệu gốc từ R2:**
  1. Liên hệ người quản lý dự án / đầu mối kỹ thuật qua **kênh bảo mật nội bộ của công ty** để được cấp thông tin xác thực S3/token truy cập vào bucket **`capitol2`** trên Cloudflare R2 (**khuyến nghị cấp token chỉ đọc — read-only** khi bàn giao nội bộ, tuyệt đối không chia sẻ rộng token có quyền ghi `write`).
  2. **Tuyệt đối không lưu thông tin xác thực R2** (Access Key ID, Secret Access Key, Endpoint token hay thông tin tài khoản) vào repository Git, biến mẫu `.env.example`, hay tài liệu `.md`.
  3. Dùng công cụ chuẩn S3 (như `rclone` hoặc `aws s3` trỏ endpoint Cloudflare R2) để tải có chọn lọc đúng phân vùng cần thiết về ổ đĩa ngoài kho Git, và luôn loại trừ thư mục `secrets/`.
