# Giới hạn đã biết (Bản 1) & Nguồn dữ liệu thô Cloudflare R2

Tài liệu này ghi minh bạch tình trạng thực tế của **Bản bàn giao 1**, những phần đang làm dở hoặc chưa kiểm tra thực tế khi đóng gói, và hướng dẫn nguồn gốc kho dữ liệu tham khảo từ Cloudflare R2.

---

## 1. Trạng thái đóng gói Bản 1 (Minh bạch kiểm chứng)

1. **Chốt `kpi_agent/` ở bản `252dc30a`:**
   - Bản bàn giao dùng commit **`252dc30a`** của `kpi_agent` vì đây là bản đã dựng hộp, chạy thực tế trên giao diện `18400`, qua kiểm tra độc lập 112 ô (14 nhân viên × 8 KPI = `152.360.000 đ`) và 14/14 ca từ chối dữ liệu lỗi.
   - Trên nhánh phát triển (`develop`) của dự án nguồn có bản mới hơn (**`fdd20ab0`**) hỗ trợ chế độ **kỳ trống** (`KPI_EMPTY_PERIOD`), nạp sổ theo dõi và trang HTML chỉ tiêu trực tiếp qua giao diện, nhưng **chưa được nối hoàn chỉnh vào giao diện trang `18400` và chưa dựng hộp kiểm chứng trên nền Capitol** nên chưa đưa vào Bản 1.
2. **Mức kiểm chứng của bản xuất GitHub:**
   - Toàn bộ gói bàn giao Bản 1 đã được **kiểm tra tĩnh** (rà cấu trúc file theo đồ thị nạp thật, kiểm tra cú pháp `py_compile`, kiểm kiểu TypeScript `tsc --noEmit`, quét khoá/bí mật = 0, quét đường dẫn gắn cứng máy cá nhân = 0, kiểm tra liên kết `.md` = 0 lỗi, đối chiếu checksum 7 `.tar`).
   - **Chưa thực hiện diễn tập dựng lại toàn bộ bằng Docker từ một máy trống hoàn toàn** trong đợt đóng gói Bản 1. Đồng nghiệp nhận bàn giao nên đối chiếu theo phiếu kiểm nhận khi dựng lần đầu và cập nhật lại nếu phát sinh sai khác môi trường.
3. **Phạm vi loại trừ ở Bản 1:**
   - Không bao gồm các module thử nghiệm/dự án khác (`sales_agent/`, `enhanced_system/`, `mekongai-social`) và bộ ghi chép đọc hiểu phân tích mã nguồn gốc.

---

## 2. Các giới hạn đã biết của hệ thống hiện tại

### 2.1 Bảo mật, đăng nhập & vận hành nhiều người
- **Chưa có đăng nhập theo từng tài khoản người dùng:** Trang `18400` dùng một mã truy cập chung (`mo_trang_18400.txt`); ai mở được trang đều thấy toàn bộ dữ liệu KPI và workflow của tổ chức.
- **Chưa có lịch sao lưu tự động:** Dữ liệu PostgreSQL, MinIO, sổ tiền và kho tài liệu nằm trên ổ đĩa/volume máy chạy; cần sao lưu thủ công định kỳ.
- **Đồng hồ WSL có thể lệch nhẹ:** Khi đồng hồ máy WSL dịch chuyển vài giây, hiệu thời gian giữa các mốc trên màn xem lượt có thể chênh lệch nhẹ.

### 2.2 Trợ lý KPI & Trang Dữ liệu
- **Chưa xử lý trọn tháng 10 hoàn toàn chỉ qua tải lên trên Bản 1 (`252dc30a`):** Danh sách tuyến, file luật tháng, luật phụ, bảng mã nhân viên vẫn được nạp sẵn lúc bật hộp `capitol-kpi-backend`. Các file tải lên qua Trang Dữ liệu ở bản `252dc30a` nằm trong bộ nhớ tiến trình nên sẽ cần tải lại nếu khởi động lại hộp KPI.
- **Tháng 11/2026 trở đi chưa chạy được:** Cần soạn thêm file luật từ công văn mới, file chỉ tiêu và danh sách tuyến tương ứng.
- **Hai khoản phụ (`19.026.300 đ` thưởng thêm và `11.868.854 đ` 1% gia vị):** Đã được `kpi_agent` tính nhưng chưa có nguồn đối chiếu độc lập ngoài hệ thống. Khoản **Hội viên** và **Thưởng vượt doanh số** chưa tính ra tiền ở tháng 10.
- **Khối Người duyệt & duyệt sau:** Khung chat trả ngay kết quả nháp (không chờ duyệt). Trên khung vẽ, nếu lượt có khối Người duyệt quá hạn không ai bấm, hệ thống tự đi tiếp ra bản nháp có nhãn và **chưa có tính năng "duyệt sau"** cho lượt đã quá hạn.

### 2.3 Lớp kiểm số, khung chat, nút Tải Excel & RAG
- **Nút "Tải Excel" dưới câu trả lời KPI trong khung chat:** Trên bản mã nguồn đóng gói ở Bản 1, điều kiện hiển thị nút vẫn kiểm tra bảng markdown trong văn bản (`tro_ly.py` bản trước khi vá), nên một số câu trả lời KPI hiển thị bảng qua thẻ kết quả cấu trúc (`the`) có thể chưa hiện nút "Tải Excel" ngay dưới câu trả lời (vẫn tải được file qua nút *"Xem file của lượt"* hoặc gõ yêu cầu xuất file).
- **Lớp AI kiểm số chỉ kiểm tra con số, chưa kiểm nghĩa:** Một câu trả lời dùng đúng con số từ công cụ nhưng diễn giải lạc ý câu hỏi vẫn có thể đi qua.
- **Bộ định tuyến khung chat:** Một số câu hỏi sát ngưỡng (như so sánh hai nhân viên) đôi khi vẫn hỏi lại nút xác nhận; một số nhãn mô tả khối trên giao diện khung vẽ vẫn giữ tên gốc trong `khoi_dung_san.json` (`"Kiểm số bằng code"` dù cấu hình thực tế dùng AI kiểm số).
- **Tính toán trên bảng tính bất kỳ từ khung chat:** Code AI viết chạy trên E2B và tính trên toàn bộ file `.xlsx`/`.csv`, nhưng con số kết quả **chưa đi qua lớp AI kiểm số** như luồng KPI.
- **Hỏi đáp tài liệu & đọc hình:**
  - Chưa hỗ trợ định dạng cũ `.doc`, `.ppt`, `.pptx`, `.xlsb`, `.ods` (hệ thống từ chối kèm hướng dẫn lưu lại sang `.docx`/`.pdf`/`.xlsx`); định dạng `.xls` cũ chưa được kiểm thử đủ rộng.
  - Với PDF kéo thẳng vào khung chat, hình/biểu đồ chỉ được phân tích khi câu hỏi có từ khoá nhắc tới hình/biểu đồ (chờ 1–2 phút để RAG mô tả hình rồi hỏi lại), và câu trả lời chưa nhúng lại ảnh biểu đồ.
  - Khi hỏi tự do trên toàn bộ kho, AI đọc tối đa 12 đoạn liên quan nhất.

### 2.4 Chạy code trên E2B & tài khoản ngoài
- **Máy ảo E2B có đường ra Internet** theo cấu hình hiện tại (khi cho phép chạy với file thật); rào bảo vệ chính là lọc đuôi file, giới hạn ≤ 20 MB, dò chuỗi giống khoá trước khi gửi lên máy ảo, không đưa API key máy chủ vào máy ảo, và máy ảo tự huỷ sau 180 giây.
- **Chưa kiểm thử lượt thực tế:** PowerPoint dùng file mẫu riêng của người dùng, lượt chạy E2B dài quá 180 giây, và khối Nghiên cứu sâu tra web trực tiếp qua trang.
- **Khoá Amazon Bedrock tạm thời:** Nếu sử dụng STS token tạm (~6 giờ), quản trị viên cần làm mới định kỳ bằng script công ty trước khi hết hạn.

---

## 3. Nguồn gốc dữ liệu thô tham khảo (Cloudflare R2 — bucket `capitol2`)

Trong quá trình nghiên cứu nền tảng Capitol AI, đội ngũ có sử dụng một bộ dữ liệu thô quy mô lớn (~97 GB, ~382 nghìn file) lưu trữ trên **Cloudflare R2** tại bucket **`capitol2`** (gồm các cấu hình workflow mẫu thực tế, nhật ký lượt chạy, bản sao lưu dữ liệu và tài liệu thử nghiệm của Capitol gốc).

- **Chính sách đóng gói Bản 1:** Bộ dữ liệu ~97 GB này **KHÔNG đưa vào GitHub và KHÔNG nén đẩy lên Google Drive** vì dung lượng quá lớn và chứa nhiều file hệ thống/nhật ký cũ của nền tảng gốc (đặc biệt thư mục `capitol2/secrets/` tuyệt đối không sao chép hoặc phát tán).
- **Khi cần lấy lại dữ liệu gốc từ R2:**
  1. Liên hệ người quản lý dự án / đầu mối kỹ thuật qua **kênh bảo mật nội bộ của công ty** để được cấp thông tin xác thực S3/token truy cập đọc vào bucket **`capitol2`** trên Cloudflare R2.
  2. **Tuyệt đối không lưu thông tin xác thực R2** (Access Key ID, Secret Access Key, Endpoint token) vào repository Git, biến mẫu `.env.example`, hay tài liệu `.md`.
  3. Dùng công cụ chuẩn S3 (như `rclone` hoặc `aws s3` trỏ endpoint Cloudflare R2) để tải có chọn lọc đúng phân vùng cần thiết về ổ đĩa ngoài kho Git, và luôn loại trừ thư mục `secrets/`.
