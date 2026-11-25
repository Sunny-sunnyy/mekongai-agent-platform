# Hướng dẫn tách phụ lục bảo mật của tài liệu kiến trúc Word

Tài liệu này mô tả cách tách dữ liệu cá nhân (họ tên thật và thu nhập từng nhân viên) ra khỏi bản tài liệu kiến trúc Word đưa lên GitHub, để đảm bảo an toàn dữ liệu khi bàn giao kho mã nguồn.

---

## 1. Hai phiên bản tài liệu kiến trúc

| Phiên bản | Nơi lưu | Nội dung |
|---|---|---|
| **Bản sạch trong kho Git** | `docs/BAO_CAO_HE_THONG.docx` (trong kho `mekongai-agent-platform`) | Giữ nguyên toàn bộ kiến trúc 3 tầng, workflow, RAG, kiểm soát an toàn, mô tả Trợ lý KPI, Phụ lục A/B/D và **tổng hợp toàn đội** (`152.360.000 đ`, `19.026.300 đ`, `11.868.854 đ`). **Đã gỡ hoàn toàn họ tên thật và mức lương từng nhân viên.** |
| **Bản đầy đủ nội bộ (có tên & thu nhập thật)** | **Google Drive bảo mật nội bộ công ty** (cùng thư mục chứa 7 `.tar`) | Bản gốc đầy đủ gồm Phụ lục C (bảng chi tiết 14 nhân viên `NV01`–`NV14` kèm họ tên thật và lương từng KPI) và ảnh màn hình xem lượt chạy chưa che tên. |

---

## 2. Chi tiết những phần đã tách / thay thế trong `docs/BAO_CAO_HE_THONG.docx`

Khi rà bản gốc, hai vị trí sau chứa tên thật và thu nhập cá nhân của nhân viên nên đã được xử lý trong bản đặt trên GitHub:

1. **Phụ lục C (`# PHỤ LỤC C. BẢNG THƯỞNG THÁNG 10/2026 THEO TỪNG NHÂN VIÊN`):**
   - *Bản gốc:* Bảng 14 dòng ghi rõ họ tên thật + lương từng KPI của từng người (`NV01`–`NV14`).
   - *Bản sạch trên GitHub:* Thay bằng bảng tổng hợp 1 dòng của cả đội (14 nhân viên, tổng `152.360.000 đ`) và ghi rõ bản chi tiết cá nhân nằm trên Google Drive nội bộ.
2. **Hình màn hình xem lại một lượt chạy ở Chương 6 (`Màn xem lại một lượt chạy`):**
   - *Bản gốc:* Ảnh chụp hiển thị tên thật và mức thưởng của 3 nhân viên đồng hạng nhất.
   - *Bản sạch trên GitHub:* Thay bằng ảnh chụp màn xem lại một lượt chạy khác **chỉ hiển thị mã nhân viên** (`NV01`–`NV13`), không lộ họ tên thật.

---

## 3. Lưu ý về ảnh minh hoạ trong báo cáo Word

Một số hình minh hoạ và ảnh chụp màn hình giữ lại từ bản báo cáo kiến trúc gốc có sai khác nhỏ so với cấu hình đang chạy ở Bản 1:
- **Sơ đồ kiến trúc (Chương 6):** Các mũi tên tới Cổng AI / Chương trình tính thưởng / Bộ tìm tài liệu / E2B xuất phát từ khung ngoài `NỀN TẢNG GỐC (CAPITOL AI)` thay vì tách riêng từng mũi tên từ ô `Bộ máy chạy`, và chưa tách cột tiêu đề `Dịch vụ bên ngoài` riêng (thay bằng dòng chú thích dưới hình).
- **Ảnh khung chat (Chương 1):** còn dòng chú thích cũ *"bảng chính thức chờ người phụ trách duyệt"* và nhắc gõ *"xuất bảng thưởng chính thức"* — trên cấu hình hiện hành, khung chat trả ngay kết quả nháp và không dừng chờ duyệt (nút duyệt chỉ chạy từ khung vẽ).
- **Ảnh khung vẽ (Chương 2):** nhãn khối kiểm còn ghi *"Kiểm số bằng code"* (khớp nhãn đang lưu trong cấu hình mẫu) — trên cấu hình hiện hành, quyết định chặn thuộc về AI kiểm số (có đáp án do chương trình tính), còn bộ luật viết sẵn chạy song song để ghi nhận đối chiếu.
- **Ảnh màn hình Người duyệt (Chương 5):** hiển thị cú pháp markdown thô (`###`, `**`) trong khung duyệt.

*(Các ảnh chụp bổ sung mới hơn như màn hình "Hỏi trên kho tài liệu", màn hình "Tính trên bảng bằng E2B" và màn hình "Tải Excel" đều sạch họ tên nhân viên và lưu sẵn trong bộ ảnh kiểm thử nếu cần cập nhật lại báo cáo sau khi dựng hệ thống).*

---

## 4. Checklist cho người đưa tài liệu lên Google Drive công ty

- [ ] Tải bản gốc `BAO_CAO_HE_THONG.docx` (có đầy đủ Phụ lục C tên thật & thu nhập) lên thư mục Google Drive riêng tư của công ty (chỉ cấp quyền xem cho lãnh đạo và người phụ trách nghiệp vụ KPI).
- [ ] Đảm bảo kho GitHub `mekongai-agent-platform` chỉ chứa bản sạch `docs/BAO_CAO_HE_THONG.docx` (đã kiểm tra không còn tên thật cá nhân).
