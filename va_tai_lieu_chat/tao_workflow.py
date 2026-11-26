"""[ghi chú] TẠO workflow "Hỏi trên tài liệu" cho khung chat (ô nhập ⇒ khối AI Claude với lời dặn doc_tai_lieu.LOI_DAN ⇒ kết quả) qua ĐÚNG đường Lưu của
trang (POST /api/save — đi qua phép kiểm của trang; 0 đồng, không gọi AI). CHỈ CHẠY SAU khi A báo "E xong" (ghi cơ sở dữ liệu của bộ đang chạy).
  python3 -I chay_thu/va_tai_lieu_chat/tao_workflow.py            # XEM TRƯỚC: in workflow sẽ lưu, KHÔNG ghi gì
  GHI=co python3 -I chay_thu/va_tai_lieu_chat/tao_workflow.py     # lưu; in mã workflow + số lỗi kiểm
Sau đó: GHI=co python3 -I chay_thu/va_mot_o_chat/gan/ghim.py (bản đã thêm thẻ hoi_tai_lieu) ⇒ the_ghim.json có thẻ ⇒ A bật lại may_chu ⇒ đường tải file hết báo 503.
Đã có workflow cùng tên ⇒ DỪNG (không tạo bản thứ hai; ghim lấy bản tạo mới nhất cùng tên). Mã mở trang đọc từ file 600, KHÔNG in.
Hình workflow lấy theo mẫu đã chạy thật 19/11 (fbfe5ec3: ô nhập ⇒ Agent gpt-6-luna ⇒ Markdown, lượt 17d73345 SUCCESS): cùng loại khối, cùng cổng nối, cùng nhiệt độ."""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

G = Path(__file__).resolve().parent
sys.path.insert(0, str(G))
import doc_tai_lieu as D  # noqa: E402
import tai_lieu_chat as T  # noqa: E402

TEN = T.TEN_WORKFLOW   # ghim.py tìm đúng tên này
d = (Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser() / "mo_trang_18400.txt").read_text().strip()
assert d.startswith("http://127.0.0.1:18400/") and "#k=" in d
GOC, _K = d.partition("#")[0].rstrip("/"), d.split("#k=", 1)[1]


def goi(duong, than=None):
    rq = urllib.request.Request(GOC + duong, data=json.dumps(than).encode() if than is not None else None,
                                headers={"Cookie": "xuong_ma_18400=" + _K, **({"Content-Type": "application/json"} if than is not None else {})})
    try:
        r = urllib.request.urlopen(rq, timeout=60); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def workflow() -> dict:
    vao, ai, ra = (str(uuid.uuid4()) for _ in range(3))
    return {"name": TEN,
            "description": "Khung chat dùng: người dùng kéo file PDF / Word vào khung chat rồi hỏi. Ô nhập nhận TOÀN VĂN tài liệu + các lượt trước + câu hỏi (máy chủ trang ghép, "
                           "có mốc mang mã). Khối AI trả lời chỉ theo tài liệu, kèm chỗ dẫn [trang N] / [đoạn N]. Đừng sửa lời dặn ở đây — sửa ở va_tai_lieu_chat/doc_tai_lieu.py rồi tạo lại + ghim lại.",
            "nodes": [{"id": vao, "type": "text_input_node", "position": {"x": 0.0, "y": 200.0}, "params": {"text_input": "(khung chat điền tài liệu + câu hỏi vào đây lúc chạy)"}},
                      {"id": ai, "type": "agent_node", "position": {"x": 400.0, "y": 200.0}, "params": {"model": "gpt-6-luna", "system_prompt": D.LOI_DAN, "temperature": 0.3}},
                      {"id": ra, "type": "markdown_output_node", "position": {"x": 800.0, "y": 200.0}, "params": {}}],
            "edges": [{"from_node": vao, "from_port": "text", "to_node": ai, "to_port": "user_prompt"},
                      {"from_node": ai, "from_port": "text", "to_node": ra, "to_port": "markdown_content"}],
            "ui": {"labels": {vao: "Tài liệu + câu hỏi", ai: "Trả lời theo tài liệu", ra: "Câu trả lời"}}}


if __name__ == "__main__":
    w = workflow()
    print(f"workflow sẽ lưu: “{w['name']}” · {len(w['nodes'])} khối ({' ⇒ '.join(n['type'] for n in w['nodes'])}) · lời dặn {len(D.LOI_DAN)} ký tự")
    # đếm trùng tên bằng SELECT thẳng cơ sở dữ liệu của tổ chức (danh sách /api/workflows của trang chỉ trả 80 workflow mới nhất ⇒ không đủ để kết luận "chưa có")
    try:
        sys.path.insert(0, str(G.parent / "giao_dien"))
        import xem_workflow as X
        org = (Path(os.environ.get("CAPITOL_RUNTIME_DIR") or (G.parent / "runtime")).expanduser() / "org_id.txt").read_text().strip()
        assert len(org) == 36 and set(org) <= set("0123456789abcdef-")
        n = X.sql("select json_build_object('n', count(*)) from workflow w where w.org_id::text = '" + org + "' and not coalesce(w.is_deleted, false) and w.name = '" + TEN.replace("'", "''") + "'")
        co = n.get("n") if isinstance(n, dict) else None
    except Exception as e:  # noqa: BLE001
        print("không đếm được:", type(e).__name__); co = None
    print("đã có workflow cùng tên (đếm trong cơ sở dữ liệu):", "KHÔNG ĐẾM ĐƯỢC" if co is None else co)
    if os.environ.get("GHI") != "co":
        print("XEM TRƯỚC — chưa ghi gì. Chạy lại với GHI=co sau khi A báo 'E xong'."); sys.exit(0)
    if co != 0:
        sys.exit("DỪNG: không chắc chưa có workflow cùng tên — không tạo bản thứ hai")
    ma, r = goi("/api/save", {"workflow": w})
    loi = [x for x in r.get("kiem", []) if x.get("muc") == "loi"]
    print(f"lưu: HTTP {ma} · mã workflow {r.get('workflow_id')} · phiên bản {r.get('version')} · lỗi kiểm {len(loi)}" + (f" · {json.dumps(loi, ensure_ascii=False)[:400]}" if loi else ""))
    sys.exit(0 if ma == 200 and r.get("workflow_id") and not loi else 1)
