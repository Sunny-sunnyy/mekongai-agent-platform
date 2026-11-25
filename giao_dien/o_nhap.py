"""[ghi chú kỹ thuật] Ô NHẬP CHỮ lúc Chạy — KHÔNG CẮT. Thay dòng cũ của may_chu.chay(): `p_["value"] = str(text_inputs[...])[:20000]` (cắt LẶNG ở 20.000 ký tự).
Vì sao: lượt thật đầu của "kéo file vào khung chat" (phiên fa71e107) — file Word 20.875 ký tự bị cắt dở ở ký tự 20.000, CÂU HỎI nằm sau tài liệu mất hẳn,
mô hình trả "chưa có câu hỏi, tài liệu bị cắt". Luật người dùng: không cắt thứ gửi cho AI; vượt chốt an toàn ⇒ DỪNG + báo, không cắt.
  kiem_co(text_inputs, tinh_tran) ⇒ None | câu báo (lời thường, nêu cỡ thật + chốt) khi có ô vượt chốt — gọi TRƯỚC khi tạo phiên / giữ chỗ (0 đồng).
  thay(payload, text_inputs) ⇒ bản sao payload đã điền NGUYÊN VĂN chữ vào ô `text_input` của đúng các khối ô nhập chữ.
Chốt 3.500.000 ký tự = rào toàn văn 3.000.000 của doc_tai_lieu + chỗ cho lịch sử cuộc trò chuyện + câu hỏi. Tiền: khối AI vẫn có trần token vào riêng của nó
(muc_toi_da.TRAN_TOKEN_VAO cho mô hình tính trần) — chốt ở đây chỉ là rào cỡ, không phải rào tiền."""
import copy

CHOT_KY_TU = 3_500_000        # rào cỡ chung (workflow KHÔNG có khối tính vào trần tiền: Claude qua Bedrock, hoặc không có AI)
CHOT_KY_TU_TINH_TRAN = 20_000  # workflow CÓ khối tính vào trần tiền: mức tối đa của trang (muc_toi_da) được dựng lúc mỗi ô nhập ≤ 20.000 ký tự (bản cũ cắt ở đó);
#                                khối kiểm token vào theo tổng ĐÃ DÙNG trước mỗi lần gọi ⇒ lần gọi ĐẦU không có trần ⇒ ô nhập dài hơn có thể vượt mức đã giữ chỗ.
#                                ⇒ giữ đúng cỡ cũ nhưng TỪ CHỐI có lời báo thay vì cắt lặng. Nới cỡ này = việc chạm tiền (tính lại mức tối đa theo cỡ ô nhập thật), làm riêng.


def _so(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def kiem_co(text_inputs, tinh_tran: bool = False) -> str | None:
    """None = qua. tinh_tran=True khi mức tối đa của lượt > 0 (có khối tính vào trần tiền)."""
    chot = CHOT_KY_TU_TINH_TRAN if tinh_tran else CHOT_KY_TU
    for ma, chu in (text_inputs or {}).items():
        n = len(str(chu))
        if n > chot:
            if tinh_tran:
                return (f"Nội dung gửi vào ô nhập dài {_so(n)} ký tự, vượt mức {_so(chot)} ký tự của workflow có khối AI tính vào trần tiền — "
                        "trang không chạy và không cắt bớt (chạy sẽ có thể tốn hơn mức tiền đã giữ chỗ). Hãy rút gọn nội dung, hoặc dùng workflow chỉ chạy Claude.")
            return (f"Nội dung gửi vào ô nhập dài {_so(n)} ký tự, vượt mức {_so(chot)} ký tự mà một lượt chạy nhận được — "
                    "trang không chạy và không cắt bớt. Hãy chia nhỏ tài liệu hoặc dùng đường nạp vào kho tài liệu rồi hỏi.")
    return None


def kiem_file(file_inputs, org: str) -> str | None:
    """[ghi chú kỹ thuật] file_inputs = {mã khối nhận file: phong bì file do CHÍNH máy chủ trang nhận từ kho file của Capitol}. Trả None | lý do từ chối.
    Phong bì phải là của ĐÚNG tổ chức đang chạy (orgid + đường s3 bắt đầu bằng files/<tổ chức>/) — không để file tổ chức khác lọt vào lượt."""
    if not file_inputs:
        return None
    if not isinstance(file_inputs, dict) or len(file_inputs) > 4:
        return "file đưa vào lượt sai dạng"
    for ma, env in file_inputs.items():
        f = env.get("file") if isinstance(env, dict) else None
        if not isinstance(ma, str) or not isinstance(f, dict) or f.get("orgid") != org or not str(f.get("s3_key") or "").startswith(f"files/{org}/") or not f.get("file_id"):
            return "file đưa vào lượt không thuộc tổ chức đang chạy (hoặc phong bì file sai dạng)"
    return None


def thay_file(payload: dict, file_inputs) -> dict:
    """Bản sao payload đã điền phong bì file vào ô `file_data` của ĐÚNG các khối nhận file. Mã khối không phải khối nhận file ⇒ ném ValueError (không điền bừa)."""
    if not file_inputs:
        return payload
    payload = copy.deepcopy(payload)
    con = set(file_inputs)
    for n in payload.get("nodes", []):
        st = (n.get("data") or {}).get("struct") or {}
        if n.get("id") in file_inputs and st.get("node_id") == "file_input_node":
            for p_ in st.get("params", []):
                if p_.get("field_id") == "file_data":
                    p_["value"] = file_inputs[n["id"]]; con.discard(n["id"])
    if con:
        raise ValueError("mã khối nhận file không có trong workflow")
    return payload


def thay(payload: dict, text_inputs) -> dict:
    if not text_inputs:
        return payload
    payload = copy.deepcopy(payload)
    for n in payload.get("nodes", []):
        st = (n.get("data") or {}).get("struct") or {}
        if n.get("id") in text_inputs and st.get("node_id") == "text_input_node":
            for p_ in st.get("params", []):
                if p_.get("field_id") == "text_input":
                    p_["value"] = str(text_inputs[n["id"]])   # NGUYÊN VĂN — không cắt
    return payload
