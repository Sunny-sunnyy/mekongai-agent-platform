"""Trang xem workflow Capitol chạy trên máy — CHỈ ĐỌC. Viết 04/11/2026 (nhóm kỹ thuật, User yêu cầu: "trực quan, sinh động,
khi chạy thì workflow cũng phải chạy theo").

Chạy:   python3 -I chay_thu/giao_dien/xem_workflow.py        rồi mở  http://127.0.0.1:18400
Dữ liệu: đọc PostgreSQL của bộ chạy thử (hộp capitol-postgres) bằng `docker exec … psql` — chỉ câu SELECT.
         Danh mục khối: chay_thu/ket_qua/b4_list_nodes.json (bản chụp danh mục thật lúc bước 4).
Không gọi API của Capitol, không gọi AI, không ghi gì. Chỉ nghe 127.0.0.1 (không mở ra mạng ngoài).

Vì sao đọc PostgreSQL mà không đọc API: bảng `workflow_event` ghi MỌI mốc của từng khối (bắt đầu, chữ AI đang viết,
xong, lỗi, chờ duyệt) kèm giờ — đủ để vẽ lượt chạy theo thời gian thực và phát lại lượt cũ.
Mã khối trong sự kiện = mã khối trong payload của phiên bản RUN (`workflow_run.version_id`) [đã chạy 04/11].
"""

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

THU_MUC = Path(__file__).resolve().parent
_RUNTIME_DIR = Path(os.environ.get("CAPITOL_RUNTIME_DIR") or (THU_MUC.parent / "runtime")).expanduser()
DANH_MUC = THU_MUC / "b4_list_nodes.json" if (THU_MUC / "b4_list_nodes.json").is_file() else (_RUNTIME_DIR / "b4_list_nodes.json")
CONG = 18400
MA = re.compile(r"^[0-9a-fA-F-]{36}$")              # chỉ nhận mã dạng UUID ⇒ không chèn được SQL
GIO = re.compile(r"^[0-9T:.+\- ]{10,40}$")
KHOA = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")          # che mọi chuỗi giống khoá OpenAI trước khi gửi ra trang


def sql(cau: str):
    """Chạy một câu SELECT trả về MỘT giá trị JSON."""
    r = subprocess.run(["docker", "exec", "capitol-postgres", "psql", "-U", "capitol", "-d", "agentic", "-tA",
                        "-v", "ON_ERROR_STOP=1", "-c", cau], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    out = r.stdout.strip()
    return json.loads(out) if out else None


def che(x):
    if isinstance(x, str):
        return KHOA.sub("sk-…(đã che)", x)
    if isinstance(x, list):
        return [che(v) for v in x]
    if isinstance(x, dict):
        return {k: che(v) for k, v in x.items()}
    return x


def gia_tri(params: list, field: str):
    for p in params or []:
        if p.get("field_id") == field:
            return p.get("value")
    return None


def do_thi(payload: dict) -> dict:
    """payload phiên bản ⇒ khối (mã, tên, loại, vị trí, cài đặt chính) + dây (nguồn ⇒ đích, ô nhận)."""
    khoi = []
    for n in payload.get("nodes", []):
        s = (n.get("data") or {}).get("struct") or {}
        pr = s.get("params") or []
        khoi.append({
            "id": n.get("id"), "ten": s.get("name") or s.get("display_name"), "loai_goc": s.get("display_name"),
            "nhom": s.get("node_type"), "ma_loai": s.get("node_id"), "icon": s.get("icon"),
            "x": (n.get("position") or {}).get("x", 0), "y": (n.get("position") or {}).get("y", 0),
            "mo_hinh": gia_tri(pr, "model"), "loi_dan": gia_tri(pr, "system_prompt"),
            "chu_nhap": gia_tri(pr, "text_input"), "han_cho": gia_tri(pr, "timeout"),
            "mo_ta": (s.get("description") or "")[:300],
        })
    day = []
    for e in payload.get("edges", []):
        day.append({"tu": e.get("source_node_id") or e.get("source"), "toi": e.get("target_node_id") or e.get("target"),
                    "o": e.get("target_param_id") or e.get("targetHandle"), "kieu": e.get("edge_type")})
    return {"khoi": khoi, "day": day, "ten": payload.get("name")}


NGAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")             # lọc theo ngày giờ VN; chỉ nhận đúng dạng YYYY-MM-DD ⇒ không chèn được SQL


def _loc(ngay: str | None, so: int | None, mac_dinh: int) -> tuple[str, int]:
    """[10/11, A — User: "bỏ giới hạn cứng, thêm xem thêm và lọc theo ngày"] ⇒ (điều kiện ngày, số dòng 1–2000)."""
    if ngay and not NGAY.match(ngay):
        raise ValueError("ngày phải dạng YYYY-MM-DD")
    dk = f"(x_tao at time zone 'Asia/Ho_Chi_Minh')::date = date '{ngay}'" if ngay else "true"
    return dk, max(1, min(int(so or mac_dinh), 2000))


def ds_luot(ngay: str | None = None, so: int | None = None):
    dk, n = _loc(ngay, so, 60)
    return sql(f"""select coalesce(json_agg(x order by x.tao desc),'[]') from (
        select r.session_id as phien, r.status::text as tt, r.created_at as tao, r.started_at as bd, r.completed_at as xong,
               left(coalesce(r.error_message,''),300) as loi, w.name as ten, w.id as wf, r.version_id as pb
        from workflow_run r join workflow w on w.id=r.workflow_id where {dk.replace('x_tao', 'r.created_at')}
        order by r.created_at desc limit {n}) x""")


def _loc_org(org: str | None) -> str:
    """[ghi chú] Mỗi lần WSL khởi động lại, tổ chức thử được tạo mới; workflow của tổ chức cũ còn trong PostgreSQL nhưng thuộc tổ chức
    cũ (lượt chạy đi theo tổ chức hiện tại) ⇒ danh sách mặc định chỉ hiện workflow của tổ chức hiện tại (12/11: 276 cái lẫn cũ / mới, cùng tên). org=None ⇒ mọi tổ chức."""
    if org is None:
        return "true"
    if not MA.fullmatch(org):
        raise ValueError("mã tổ chức không hợp lệ")
    return f"org_id::text = '{org}'"


def dem(org: str | None = None):
    """Tổng workflow / lượt chạy, chia theo ngày (giờ VN) — để trang ghi "đang hiện X / tổng Y" và dựng ô chọn ngày."""
    lo = _loc_org(org)
    return sql(f"""select json_build_object(
        'wf', (select count(*) from workflow where not coalesce(is_deleted,false) and {lo}),
        'luot', (select count(*) from workflow_run),
        'ngay', (select coalesce(json_agg(json_build_object('ngay', d, 'wf', w, 'luot', l) order by d desc),'[]') from (
            select d, sum(w) w, sum(l) l from (
              select (created_at at time zone 'Asia/Ho_Chi_Minh')::date d, 1 w, 0 l from workflow where not coalesce(is_deleted,false) and {lo}
              union all select (created_at at time zone 'Asia/Ho_Chi_Minh')::date, 0, 1 from workflow_run) t group by d) g))""")


def mot_luot(phien: str, sau: str | None):
    r = sql(f"""select row_to_json(x) from (select r.id, r.session_id as phien, r.status::text as tt, r.created_at as tao,
        r.started_at as bd, r.completed_at as xong, left(coalesce(r.error_message,''),600) as loi, w.name as ten, r.version_id as pb,
        (select payload from workflow_version v where v.id=r.version_id) as payload
        from workflow_run r join workflow w on w.id=r.workflow_id where r.session_id='{phien}' order by r.created_at desc limit 1) x""")
    if not r:
        return None
    # Giờ ghi có lúc đi lùi 1–1,5 giây (đồng hồ WSL bị chỉnh — D phát hiện 04/11) ⇒ lấy thêm theo created_at và GỐI LÙI
    # 5 giây, trang tự bỏ trùng theo id; nếu chỉ lấy "timestamp > mốc cũ" thì sự kiện ghi sau một lần lùi giờ bị mất hẳn.
    dk = f"and e.created_at > timestamptz '{sau}' - interval '5 seconds'" if sau else ""
    # chữ "processing" (AI đang viết / đang nghĩ) gửi nguyên mẩu; mốc khác cắt 4.000 ký tự cho nhẹ
    ev = sql(f"""select coalesce(json_agg(json_build_object(
            'id', e.id, 'c', e.created_at, 'n', coalesce(e.node_instance_id, e.event_data->'data'->>'node_id'), 't', e.event_type, 'ts', e.timestamp,
            'k', e.event_data->>'display_type',
            'd', case when e.event_type='processing' then e.event_data->>'data'
                      when e.event_type='node_result' then left(e.event_data->'data'->>'value', 4000)
                      when e.event_type='awaiting_intervention' then left(e.event_data->'data'->>'preview_context', 4000)
                      when e.event_type='execution_failed' then left(e.event_data->'data'->>'error', 1000)
                      when e.event_type='lifecycle' then e.event_data->'data'->>'resolution'
                      when e.event_type='input_issue' then left(coalesce(e.event_data->>'issue_kind','') || ' · ' || coalesce(e.event_data->>'message',''), 1000)
                      when jsonb_typeof(e.event_data->'data')='string' then left(e.event_data->>'data', 4000)
                      else left(coalesce(e.event_data->>'message', ''), 1000) end
        ) order by e.created_at, e.timestamp, e.id),'[]') from workflow_event e where e.run_id='{r["id"]}' {dk}""")
    out = {"luot": {k: v for k, v in r.items() if k != "payload"}, "su_kien": ev}
    try:   # [ghi chú] chữ lỗi lớp chặn "chan" ⇒ lời dễ hiểu (chỉ hiển thị); lỗi ở đây KHÔNG làm hỏng trang
        import loi_de_hieu
        loi_de_hieu.ap_dung(out)
    except Exception:  # noqa: BLE001
        pass
    if not sau:
        out["do_thi"] = do_thi(r.get("payload") or {})
    return out


def ds_workflow(ngay: str | None = None, so: int | None = None, org: str | None = None):
    dk, n = _loc(ngay, so, 80)
    dk += " and w." + _loc_org(org) if org else ""
    return sql(f"""select coalesce(json_agg(x order by x.tao desc),'[]') from (
        select w.id, w.name as ten, w.created_at as tao,
               (select count(*) from workflow_version v where v.workflow_id=w.id) as so_pb,
               (select count(*) from workflow_run r where r.workflow_id=w.id) as so_luot
        from workflow w where not coalesce(w.is_deleted,false) and {dk.replace('x_tao', 'w.created_at')}
        order by w.created_at desc limit {n}) x""")


def mot_workflow(wf: str):
    p = sql(f"""select payload from workflow_version where workflow_id='{wf}' and version_type::text <> 'RUN'
                order by version_number desc limit 1""")
    if p is None:
        p = sql(f"select payload from workflow_version where workflow_id='{wf}' order by version_number desc limit 1")
    return do_thi(p or {})


def danh_muc():
    d = json.loads(DANH_MUC.read_text())
    ra = []

    def di(x):
        if isinstance(x, dict):
            if "node_id" in x and "params" in x:
                ra.append({"ma": x["node_id"], "ten": x.get("display_name"), "nhom": x.get("node_type"),
                           "mo_ta": (x.get("description") or "")[:240], "icon": x.get("icon"), "beta": x.get("beta")})
                return
            for v in x.values():
                di(v)
        elif isinstance(x, list):
            for v in x:
                di(v)
    di(d)
    return ra


class XuLy(BaseHTTPRequestHandler):
    def _gui(self, code: int, body: bytes, kieu: str):
        self.send_response(code)
        self.send_header("Content-Type", kieu)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._gui(code, json.dumps(che(obj), ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._gui(200, (THU_MUC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/luot":
                return self._json(ds_luot())
            m = re.match(r"^/api/luot/([^/]+)$", u.path)
            if m:
                phien, sau = m.group(1), (q.get("sau") or [None])[0]
                if not MA.match(phien) or (sau and not GIO.match(sau)):
                    return self._json({"loi": "mã không hợp lệ"}, 400)
                r = mot_luot(phien, sau)
                return self._json(r if r else {"loi": "không thấy lượt"}, 200 if r else 404)
            if u.path == "/api/workflow":
                return self._json(ds_workflow())
            m = re.match(r"^/api/workflow/([^/]+)$", u.path)
            if m:
                if not MA.match(m.group(1)):
                    return self._json({"loi": "mã không hợp lệ"}, 400)
                return self._json(mot_workflow(m.group(1)))
            if u.path == "/api/danh_muc":
                return self._json(danh_muc())
            return self._json({"loi": "không có đường này"}, 404)
        except Exception as e:  # trang vẫn sống khi hộp PostgreSQL tắt — báo lỗi cho người xem
            return self._json({"loi": f"{type(e).__name__}: {e}"}, 503)

    def log_message(self, *a):  # im lặng — trang hỏi mỗi giây
        pass


if __name__ == "__main__":
    print(f"Trang xem workflow: http://127.0.0.1:{CONG}  (Ctrl+C để tắt)")
    ThreadingHTTPServer(("127.0.0.1", CONG), XuLy).serve_forever()
