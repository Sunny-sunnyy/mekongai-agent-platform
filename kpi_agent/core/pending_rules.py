"""Khối 6 — ba luật công văn cần dữ liệu từng dòng đơn hàng, sổ Tracking không có (xem HIEU_DU_LIEU.md mục 5).

Không giả lập: mỗi luật trả "chưa kiểm được — thiếu dữ liệu" kèm cột cần có. Khi có file xuất DMS từng dòng đơn,
viết hàm kiểm thật ở đây và gọi từ workflow trước khối 3.
"""
NOT_CHECKED = "chưa kiểm được — thiếu dữ liệu"

PENDING_RULES = [
    {"rule": "MOQ tính theo từng line, không cộng dồn mã, mã Z vẫn phải đủ MOQ; nhóm mục 8.2 phải đạt cả MOQ tổng nhóm",
     "cong_van": "mục 2.2, 2.3, 2.7, 2.8, 8.1, 8.2",
     "needs": ["ORDER NUM", "SKU CODE", "số lượng", "đơn vị", "vùng"],
     "today": "dùng cờ MOQ_FLAG=Y hệ thống DMS gắn sẵn trong pivot"},
    {"rule": "tối đa 1 PC (PC_BT, PC_ON, đơn của LPPC / LPPC_Meat) mỗi ngày mỗi cửa hàng", "raw_checks": True,
     "cong_van": "mục 2.2, 2.3, 2.7, 2.8",
     "needs": ["ORDER NUM", "OUTLET CODE", "ngày phát sinh đơn"],
     "today": "khối PC_BT không có cột ngày lẫn cửa hàng ⇒ mỗi đơn đủ điều kiện tính 1 PC"},
    {"rule": "đơn phải xuất hoá đơn trước 22h ngày 02 tháng T+1; tính theo ngày phát sinh đơn", "raw_checks": True,
     "cong_van": "mục 6.1",
     "needs": ["ngày phát sinh đơn", "thời điểm xuất hoá đơn"],
     "today": "theo bộ lọc ORDER STATUS của pivot (không rõ gồm trạng thái nào)"},
]


def pending_rule_status(source: str = "tracking") -> list[dict]:
    """Luật CHƯA kiểm được với nguồn dữ liệu đang dùng. Đường RAW DATA (10/11, `core/load_raw.py`) có ngày đơn + cửa hàng + giờ xuất hoá đơn từng dòng
    ⇒ kiểm 2 luật `raw_checks` (bỏ khỏi danh sách); luật MOQ từng mã vẫn dùng cờ MOQ_FLAG của DMS (RAW không có số lượng tối thiểu theo mã)."""
    return [{key: value for key, value in rule.items() if key != "raw_checks"} | {"status": NOT_CHECKED}
            for rule in PENDING_RULES if not (source == "raw" and rule.get("raw_checks"))]
