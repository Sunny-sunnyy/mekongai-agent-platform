"""Chạy workflow KPI trên BẢN DỮ LIỆU vừa tải lên (đối chiếu kỹ thuật).

Vì sao có file này: hộp Capitol đã có ô hệ thống `kpi_data_version` ở khối Agent KPI + khối Kiểm số (sua_doi/nodes/agent/params.py:91,
native_tools.py:453), nhưng trang chưa bao giờ điền ô đó ⇒ mọi lượt tính trên bản lúc bật backend dù người dùng vừa tải file mới.

Đường đi (máy chủ trang, KHÔNG tin trang):
  doc_ban(x)          ⇒ None (rỗng / chỉ khoảng trắng = bản lúc bật) | chuỗi hex thường 1–64 | ValueError (mọi dạng khác)
  ghi_ban(kq)         ⇒ nhớ {mã bản: kỳ, ngày chốt} từ chính kết quả /api/kpi/tai-len (backend trả) — chỉ trong bộ nhớ tiến trình
  dien(payload, dv, …) ⇒ (http, lỗi) hoặc (200, bản sao payload đã điền) — gọi TRƯỚC giữ chỗ tiền, TRƯỚC lần gọi AI đầu tiên:
      1. workflow không phải KPI ⇒ 400 (không bỏ qua lặng)
      2. mã bản chưa thấy qua trang này ⇒ 409 "tải lại file" (máy chủ trang không biết kỳ / ngày chốt của bản đó)
      3. kỳ / ngày chốt của bản ≠ của bản đang bật ⇒ 409: lời dặn Trợ lý KPI ghi kỳ / ngày chốt của bản đang bật (luật D lúc chạy so từng ký tự)
         ⇒ chạy trên bản khác ngày chốt thì AI ghi sai thời điểm. Hỗ trợ bản khác ngày chốt = việc tuần 8 (điền lại lời dặn theo bản).
      4. hỏi backend bản đó còn không (/api/tools?data_version=, backend trả 404 khi lạ) ⇒ không còn ⇒ 409 (ca V1 nhóm kỹ thuật: dừng trước AI)
      5. điền ô kpi_data_version ở MỌI agent_node bật tool_kpi + MỌI guardrails_node; Agent không bật tool_kpi KHÔNG điền (ca V3);
         thiếu ô (workflow lưu trước khi vá) ⇒ 409, không chạy thiếu
  Bản đã lưu trong DB không đổi: chỉ điền vào bản sao của lượt (ca V2).
"""
import copy
import json
import re
import subprocess

_HEX = re.compile(r"[0-9a-f]{1,64}")
_BAN: dict[str, dict] = {}   # mã bản ⇒ {"period", "cutoff"} — từ kết quả tải lên mà backend trả qua trang này


def doc_ban(x):
    if x is None:
        return None
    if not isinstance(x, str):
        raise ValueError("ô data_version phải là chuỗi")
    if x.strip() == "":
        return None          # V8: rỗng / chỉ khoảng trắng = bản lúc bật (trang không gửi gì cũng vậy)
    if not _HEX.fullmatch(x):
        raise ValueError("ô data_version chỉ nhận chuỗi hex thường 1–64 ký tự (mã bản trang nhận khi tải file)")
    return x


def ghi_ban(kq: dict) -> None:
    dv = (kq or {}).get("data_version")
    if isinstance(dv, str) and _HEX.fullmatch(dv):
        _BAN[dv] = {"period": kq.get("period"), "cutoff": kq.get("cutoff")}


def _la_bat(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "on", "yes"))


def _o(st: dict, f: str):
    return next((p for p in st.get("params", []) if p.get("field_id") == f), None)


def _backend_con(hop: str, dv: str) -> int | None:
    """HTTP của GET /api/tools?data_version=<dv> gọi từ hộp worker (cùng đường native_tools dùng). dv đã qua _HEX ⇒ ghép vào lệnh an toàn."""
    ma = ("import urllib.request as u,urllib.error as e\n"
          "try:\n u.urlopen('http://kpi-backend:8110/api/tools?data_version=" + dv + "',timeout=10).read();print(200)\n"
          "except e.HTTPError as x:\n print(x.code)")
    try:
        r = subprocess.run(["docker", "exec", hop, "python", "-I", "-c", ma], capture_output=True, text=True, timeout=40)
        return int((r.stdout or "").strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 — không hỏi được ⇒ người gọi coi là "không chắc" ⇒ không chạy
        return None


def dien(payload: dict, dv: str, *, la_kpi, meta, hop: str, hoi_backend=_backend_con) -> tuple[int, dict]:
    nodes = payload.get("nodes") or []
    if not la_kpi(nodes):
        return 400, {"loi": "Workflow này không phải workflow KPI — không chọn bản dữ liệu KPI cho nó được."}
    ban = _BAN.get(dv)
    if ban is None:
        return 409, {"loi": "Máy chủ trang chưa thấy bản dữ liệu này (máy chủ vừa bật lại?) — tải lại file ở trang “Dữ liệu” rồi bấm “Dùng số liệu này để hỏi”."}
    if not meta:
        return 503, {"loi": "Chưa đọc được kỳ / ngày chốt của backend KPI — chờ vài giây rồi bấm lại."}
    if (ban["period"], ban["cutoff"]) != (meta.get("period"), meta.get("cutoff")):
        return 409, {"loi": f"Bản dữ liệu này là kỳ {ban['period']}, ngày chốt {ban['cutoff']} — khác bản đang chạy (kỳ {meta.get('period')}, "
                            f"ngày chốt {meta.get('cutoff')}). Lời dặn của Trợ lý KPI ghi ngày chốt của bản đang chạy nên chưa chạy được trên bản này. "
                            "Tải lại file với đúng ngày chốt của bản đang chạy, hoặc chờ bản hỗ trợ đổi ngày chốt."}
    code = hoi_backend(hop, dv)
    if code != 200:
        return 409, {"loi": "Backend KPI không còn bản dữ liệu này (backend bật lại sẽ mất bản đã tải) — tải lại file." if code == 404
                     else f"Không hỏi được backend KPI bản này còn không (mã {code}) — chưa chạy gì."}
    moi = copy.deepcopy(payload)
    thieu, so = [], 0
    for n in moi.get("nodes") or []:
        st = (n.get("data") or {}).get("struct") or {}
        loai = st.get("node_id")
        if loai == "agent_node" and not _la_bat((_o(st, "tool_kpi") or {}).get("value")):
            continue
        if loai not in ("agent_node", "guardrails_node"):
            continue
        o = _o(st, "kpi_data_version")
        if o is None:
            thieu.append(st.get("name") or n.get("id"))
        else:
            o["value"] = dv
            so += 1
    if thieu:
        return 409, {"loi": f"Khối chưa có ô bản dữ liệu (workflow lưu trước khi vá): {thieu[:5]} — làm mới danh mục rồi lưu lại workflow."}
    return 200, {"payload": moi, "so_khoi": so}


if __name__ == "__main__":   # thử nhanh không mạng: python3 -I ban_du_lieu.py
    print(json.dumps({"rong": doc_ban("  "), "dung": doc_ban("ab12")}))
