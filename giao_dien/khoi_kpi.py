"""KHỐI KPI DỰNG SẴN — phần máy chủ trang (C 12/11/2026; User: "tạo sẵn các khối, điền sẵn system prompt, tools, tạo workflow mẫu… tôi chỉ biết kéo thả").
Dữ liệu + luật là nhóm kỹ thuật, MỘT nguồn: chay_thu/kpi_capitol/khoi_dung_san.py (5 khối, bộ sẵn, mẫu, kiem_khoi_dung_san) + workflow_kpi.py (lời dặn,
kiem_payload). File này chỉ NỐI vào trang: không có danh sách khối / ô khoá / chữ lời dặn riêng.

Trang lấy:  ds()            ⇒ danh sách khối dựng sẵn + bộ sẵn + mẫu (kèm mã workflow mẫu đã lưu) + lời dặn / công cụ ĐỂ XEM
            bo_san(cau, ma) ⇒ "Bộ KPI sẵn" (5 khối 5 dây) hoặc MỘT khối, ở hình của trang, dựng bằng khoi_dung_san.tao_bo_san (không tự ghép)
Máy chủ kiểm (không tin trang):  kiem(payload) lúc LƯU và lúc CHẠY ⇒ danh sách lỗi lời thường (rỗng = đạt / không phải workflow KPI).
Khoá ô trên trang chỉ là HIỂN THỊ; ai gửi thẳng API với ô khoá bị đổi thì kiem() chặn.
"""
import json
import re
import subprocess
import threading
import time

_khoa = threading.Lock()
_nho = {"luc": 0.0, "mt": None}
NHO_GIAY = 60

# Lời của workflow_kpi.kiem_payload ⇒ lời thường (lời của kiem_khoi_dung_san đã là lời thường, hiện nguyên văn)
_LOI_THUONG = (
    (r"không có Rào chắn", "Thiếu khối “Kiểm số KPI”. Mọi câu trả lời có số KPI phải đi qua khối này."),
    (r"Đ-3b Rào chắn", "Khối Kiểm số KPI phải giữ cấu hình chế độ rào chuẩn của hệ thống."),
    (r"Đ-20", "Khối Trợ lý KPI và khối Kiểm số phải dùng CÙNG một bản dữ liệu."),
    (r"Đ-10b", "Workflow KPI không được chứa khối đưa dữ liệu ra ngoài hoặc lấy nội dung web. Tách sang workflow khác."),
    (r"không có Agent bật tool_kpi", "Không có khối Trợ lý KPI — khối Kiểm số / Giám sát duyệt chỉ dùng cho workflow KPI."),
    (r"Đ-2 Agent", "Câu trả lời của Trợ lý KPI đang đi tới khối khác mà KHÔNG qua khối Kiểm số. Nối Trợ lý KPI ⇒ Kiểm số trước."),
    (r"Đ-2 khối", "Khối kết quả / Giám sát duyệt phải đứng SAU khối Kiểm số."),
    (r"Đ-19", "Ô “câu hỏi” của khối Kiểm số phải nối bằng đúng MỘT dây từ khối Câu hỏi giám sát (không gõ sẵn, không nối từ khối khác)."),
    (r"Đ-3b Người duyệt", "Khối Giám sát duyệt phải bật “chế độ KPI”."),
    (r"Đ-6", "Khối Giám sát duyệt phải nhận đúng MỘT dây vào, từ khối Kiểm số."),
    (r"tự duyệt khi hết giờ", "Khối Giám sát duyệt không được bật “tự duyệt khi hết giờ”."),
)
_CHUA_DUYET = "tạm tính — chưa phải số chốt, chưa duyệt"   # câu cố định của backend KPI khi bảng chưa được soát (store.py UNREVIEWED — B ghi ở khoi_dung_san._NGU_CANH)
# [D K2] ô bật / tắt quyết định "đây là workflow KPI" + đường ra ngoài: áp cho MỌI workflow — không được nhận giá trị qua dây
_O_CO = {"agent_node": ("tool_kpi", "tool_web_search", "tool_fetch_url", "mcp_servers", "kpi_data_version"),
         "guardrails_node": ("che_do_kiem", "kpi_data_version"), "human_intervention_node": ("che_do_kpi", "auto_approve_on_timeout")}


def moi_truong(H, org: str) -> dict:
    """Danh mục khối đang chạy + thông tin kỳ của backend KPI + bản chuẩn 5 khối. Nhớ 60 giây. Lỗi ⇒ ném (người gọi coi là "không kiểm được")."""
    with _khoa:
        if _nho["mt"] and time.time() - _nho["luc"] < NHO_GIAY and _nho["mt"]["org"] == org:
            return _nho["mt"]
        import khoi_dung_san as KD   # chay_thu/kpi_capitol đã ở sys.path (hoi_kpi thêm)
        W, CM = H.W, H.CM
        _, catalog = CM.goi("POST", f"/nodes/{org}/list-nodes", {})
        W.kiem_danh_muc(CM.tim_khoi, catalog)
        try:
            meta = H.chay_m3.meta()
        except Exception:  # noqa: BLE001
            meta = {}
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str((meta or {}).get("cutoff") or "")):
            meta = None      # backend KPI chưa sẵn: vẫn kiểm được (lời dặn so phần cố định + khuôn dòng ngữ cảnh), nhưng KHÔNG dựng được bộ sẵn
        mt = {"org": org, "KD": KD, "W": W, "CM": CM, "catalog": catalog, "meta": meta, "chuan": KD.khoi_chuan(CM.Ve, catalog, meta)}
        _nho.update(luc=time.time(), mt=mt)
        return mt


def _cong_cu(hop: str) -> list[dict]:
    """Tên + mô tả 1 dòng của công cụ KPI (GET /api/tools của backend, đọc từ hộp worker) — chỉ để XEM trên trang."""
    ma = ("import json,urllib.request as u;t=json.load(u.urlopen('http://kpi-backend:8110/api/tools',timeout=10));"
          "t=t if isinstance(t,list) else t.get('tools',[]);"
          "print(json.dumps([{'ten':(x.get('function') or x).get('name'),'mo_ta':((x.get('function') or x).get('description') or '').split('\\n')[0][:200]} for x in t]))")
    try:
        r = subprocess.run(["docker", "exec", hop, "python", "-I", "-c", ma], capture_output=True, text=True, timeout=40)
        return [x for x in json.loads(r.stdout or "[]") if isinstance(x.get("ten"), str)]
    except Exception:  # noqa: BLE001
        return []


def ds(H, org: str, ds_wf) -> dict:
    mt = moi_truong(H, org)
    KD = mt["KD"]
    if "cong_cu" not in mt:              # [D minor 1] cùng bộ nhớ đệm 60 giây với danh mục + meta (không docker exec mỗi lần mở trang)
        mt["cong_cu"] = _cong_cu(H.chay_m3.HOP)
    d = KD.xuat_json(mt["chuan"])
    wf: dict = {}
    for w in ds_wf() or []:          # [ghi chú] ds_wf trả MỚI NHẤT TRƯỚC ⇒ giữ bản gặp đầu tiên của mỗi tên (trước đây bản cũ đè bản mới)
        wf.setdefault(w["ten"], w["id"])
    for m in d["mau"]:
        m["id"] = wf.get(m["ten"])
    d["loi_dan"] = KD._loi_dan_tu_meta(mt["meta"]) if mt["meta"] else None   # [D minor 2] CHỈ ĐỂ XEM; bản dùng lúc chạy do máy chủ so, không lấy từ trang
    d["cong_cu"] = mt["cong_cu"]
    d["ky"] = {k: (mt["meta"] or {}).get(k) for k in ("period", "cutoff", "rule_version")}
    d["san_sang"] = mt["meta"] is not None
    return d


def bo_san(H, org: str, cau, ma, sang_hinh) -> tuple[int, dict]:
    """Bộ sẵn (ma=None) hoặc MỘT khối dựng sẵn (ma = mã preset), ở hình của trang. `sang_hinh(payload)` = sang_chung(tu_capitol(...)) của may_chu."""
    mt = moi_truong(H, org)
    KD = mt["KD"]
    if mt["meta"] is None:
        return 503, {"loi": "Backend KPI chưa sẵn (chưa đọc được kỳ / ngày chốt) — chờ vài giây rồi thử lại."}
    cau = (cau if isinstance(cau, str) and cau.strip() else KD.MAU[0]["cau_hoi"]).strip()[:1000]
    v, k = KD.tao_bo_san(mt["CM"].Ve, mt["catalog"], cau, mt["meta"])
    if ma is None:
        hinh = sang_hinh({"id": "", "name": "KPI của tôi", "description": "Dựng từ “Bộ KPI sẵn” — chỉ cần sửa câu hỏi rồi bấm Lưu rồi chạy.",
                          "nodes": v.nodes, "edges": v.edges})
        hinh.pop("id", None)
        return 200, hinh
    khoa = KD.TEN_TRONG_DUNG.get(ma)
    if khoa not in k:
        return 404, {"loi": "Không có khối dựng sẵn này."}
    hinh = sang_hinh({"id": "", "name": "x", "description": "", "nodes": [n for n in v.nodes if n["id"] == k[khoa]], "edges": []})
    n = hinh["nodes"][0]
    return 200, {"type": n["type"], "params": n["params"], "label": (hinh.get("ui") or {}).get("labels", {}).get(n["id"]) or ma}


def dien_loi_dan(H, org: str, payload: dict) -> int:
    """Lúc LƯU: lời dặn của khối Trợ lý KPI là ô KHOÁ do hệ thống điền ⇒ máy chủ điền LẠI bản hiện hành (workflow_kpi.loi_dan theo kỳ / ngày chốt /
    bản luật / trạng thái của hộp đang bật). Nhờ vậy "mở workflow rồi bấm Lưu lại" thật sự làm mới được workflow bị chặn vì bảng thưởng đã đổi.
    CHỈ thay khi lời dặn đang có ĐÚNG khuôn (phần cố định từng ký tự + dòng ngữ cảnh code điền) — lời dặn bị sửa tay thì để nguyên cho kiem() chặn.
    Trả số khối đã điền lại. Lỗi / chưa đọc được meta ⇒ 0 (không đổi gì)."""
    try:
        import khoi_dung_san as KD
        mt = moi_truong(H, org)
        if mt["meta"] is None or not KD.la_workflow_kpi(payload.get("nodes") or []):
            return 0
        moi, n = KD._loi_dan_tu_meta(mt["meta"]), 0
        for nd in payload["nodes"]:
            s = nd["data"]["struct"]
            if s.get("node_id") != "agent_node":
                continue
            for q in s.get("params", []):
                if q.get("field_id") == "system_prompt" and q.get("value") != moi and KD.loi_dan_dung(q.get("value"), None):
                    q["value"] = moi
                    n += 1
        return n
    except Exception:  # noqa: BLE001
        return 0


def kiem(H, org: str, payload: dict, luc_chay: bool = False) -> list[str]:
    """Lỗi luật KPI của một payload Capitol bằng lời thường. Rỗng = đạt hoặc không phải workflow KPI. Không kiểm được ⇒ coi là lỗi (không cho qua)."""
    try:
        import khoi_dung_san as KD
        W = H.W
        nodes, edges = payload.get("nodes") or [], payload.get("edges") or []
        st = {n["id"]: n["data"]["struct"] for n in nodes}
        loai = {k: v.get("node_id") for k, v in st.items()}
        ten = lambda k: str(st[k].get("name") or loai[k])   # noqa: E731

        def co_day(k, o):
            for e in edges:
                dich = e.get("target") or e.get("target_node_id") or e.get("targetNodeId")
                cong = [str(e.get(x) or "") for x in ("target_param_id", "target_port_id", "targetHandle")]
                if dich == k and any(c == o or o in c for c in cong):
                    return True
            q = next((x for x in st[k].get("params", []) if x.get("field_id") == o), {}) or {}
            ket = (((q.get("bindable_config") or {}).get("input_port") or {}).get("incoming_connections")) or []
            return any(c.get("node_id") for c in ket if isinstance(c, dict))
        loi_day = [f"Ô “{o}” của khối “{ten(k)}” đang nhận giá trị qua dây nối — ô bật / tắt này chỉ được đặt sẵn trong khối, không được nối dây."
                   for k in st for o in _O_CO.get(loai[k], ()) if co_day(k, o)]
        if loi_day:
            return loi_day
        if not KD.la_workflow_kpi(nodes):
            return []
        mt = moi_truong(H, org)
        loi = []
        try:
            # [đã soát lần gắn 3] LƯU: meta=None — lời dặn so phần CỐ ĐỊNH từng ký tự + dòng ngữ cảnh đúng khuôn (workflow dựng cho lần bật khác vẫn
            # lưu được). CHẠY: meta THẬT của hộp KPI đang bật — dòng ngữ cảnh (kỳ, ngày chốt, bản luật, trạng thái) phải khớp từng ký tự, vì AI được dặn
            # một ngày chốt khác với dữ liệu công cụ trả thì câu trả lời ghi sai thời điểm. Lúc chạy không đọc được meta ⇒ chặn.
            if luc_chay and mt["meta"] is None:
                return ["Chưa đọc được kỳ / ngày chốt của backend KPI nên chưa chạy được workflow KPI — chờ vài giây rồi bấm lại."]
            meta, lp = None, None
            if luc_chay:
                # So CHẶT kỳ + ngày chốt + bản luật với hộp đang bật. "Trạng thái" đổi mỗi khi có người DUYỆT bảng thưởng ⇒ so chặt cả hai chiều thì sau
                # lần duyệt đầu mọi workflow đã lưu bị chặn oan. [D phán — MỘT CHIỀU] trạng thái trong dòng ngữ cảnh hợp lệ khi (a) BẰNG từng ký tự
                # trạng thái của hộp, HOẶC (b) đúng câu cố định "chưa duyệt" (nói THẤP hơn thật — vô hại). Workflow ghi "đã soát lúc …" mà hộp đang
                # tạm tính / đã soát giờ khác ⇒ chặn (lời dặn bảo AI bảng đã được duyệt trong khi không phải; Rào chỉ kiểm số, không kiểm chữ này).
                meta = dict(mt["meta"])
                lp = next((W._val(st[k], "system_prompt") for k in st if loai[k] == "agent_node"), None)
                if isinstance(lp, str) and lp.endswith("; trạng thái: " + _CHUA_DUYET + ".") and KD.loi_dan_dung(lp, None):
                    meta["status_label_for_model"] = _CHUA_DUYET
            KD.kiem_khoi_dung_san(nodes, mt["chuan"], meta, edges)
        except ValueError as e:
            manh = [x.strip()[:1].upper() + x.strip()[1:] + "." for x in str(e).split(": ", 1)[-1].split("; ") if x.strip()]
            if luc_chay and lp and KD.loi_dan_dung(lp, None):
                # lời dặn ĐÚNG khuôn nhưng khác hộp đang bật (kỳ / ngày chốt / bản luật / dấu "đã soát") ⇒ nói đúng việc cần làm, không nói "bị sửa"
                manh = [("Khối “Trợ lý KPI”: bảng thưởng (kỳ, ngày chốt, bản luật hoặc dấu đã soát) đã đổi sau lần lưu này — mở workflow và bấm Lưu lại, "
                         "hoặc thả lại “Bộ KPI sẵn”.") if "lời dặn" in x else x for x in manh]
            loi += manh
        agent_kpi = [k for k in st if loai[k] == "agent_node" and W._bat(W._val(st[k], "tool_kpi"))]
        try:
            W.kiem_payload(nodes, edges, can_agent=bool(agent_kpi))   # không có Trợ lý KPI (workflow thử câu giả đi thẳng vào Kiểm số): kiểm phần còn lại
        except ValueError as e:
            for manh in str(e).split(": ", 1)[-1].split("; "):
                c = next((t for mau, t in _LOI_THUONG if re.search(mau, manh)), "Workflow KPI chưa đạt luật nối khối.")
                if c not in loi:
                    loi.append(c)
        if loi:
            loi.append("Nếu đây là workflow thử hoặc bản KPI lưu trước khi có khối dựng sẵn: mở mục “Mẫu KPI” hoặc bấm “Bộ KPI sẵn” để tạo bản mới.")
        return loi
    except Exception as e:  # noqa: BLE001
        return [f"Không kiểm được luật KPI của workflow này ({type(e).__name__}) — chưa cho lưu / chạy."]
