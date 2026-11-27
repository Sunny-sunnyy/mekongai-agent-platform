# Build mẫu E2B (sandbox chạy mã Python đọc tài liệu Office)

Thư mục này lưu tệp khóa phiên bản thư viện để dựng lại mẫu E2B (`chay-thu-e2b-doc:v2`) phục vụ các khối đọc tài liệu Office và chạy mã trong môi trường cô lập.

## Thành phần

- `requirements_e2b_doc.txt`: danh sách thư viện Python gắn phiên bản cố định (`pandas`, `openpyxl`, `python-docx`, `pypdf`, `pdfplumber`, `python-pptx`, `pyxlsb`, `xlrd`, `reportlab`, `matplotlib`, `pillow`, ...).

## Điều kiện trước khi dựng

1. Đã có tài khoản E2B và điền khóa `CHAY_THU_E2B_KHOA` vào tệp `env/e2b.khoa.env`.
2. Đã chuẩn bị mã khung mẫu trong thư mục `va_e2b/bac2/` và bộ hàm đọc tài liệu trong `va_e2b/mekong_doc/`.
3. Trên máy đã bật Docker daemon (vì bước dựng mẫu đẩy container lên E2B thông qua script dựng).

## Lệnh dựng mẫu

Chạy từ thư mục chạy hệ thống (`chay_thu/`):

```bash
set -a
source env/e2b.khoa.env
set +a
python3 va_e2b/bac2/dung_template_bac2.py
```

Sau khi dựng thành công, cập nhật `CHAY_THU_E2B_TEMPLATE` (tên/ID mẫu vừa dựng, mặc định nếu bỏ trống là `code-interpreter-v1`) vào `env/e2b.khoa.env` và khởi động lại dịch vụ nền tảng nếu cần.
