"""[ghi chú kỹ thuật] Bộ mở HTTP KHÔNG theo đổi hướng — dùng cho mọi lời gọi hộp 18402 có kèm MÃ RIÊNG của máy chủ trang.
urllib mặc định đi theo 3xx và mang theo đầu mục riêng sang địa chỉ mới; ở đây 3xx ⇒ urllib.error.HTTPError (mã 3xx) ⇒ người gọi xử như một mã lỗi, mã riêng không rời đi."""
import urllib.request


class _KhongDoiHuong(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: PLR0913 — chữ ký của thư viện
        return None   # None ⇒ urllib ném HTTPError(code) thay vì gọi tiếp newurl


_MO = urllib.request.build_opener(_KhongDoiHuong)


def mo(req, timeout):
    """Như urllib.request.urlopen(req, timeout=…) nhưng KHÔNG theo đổi hướng."""
    return _MO.open(req, timeout=timeout)
