"""[ghi chú kỹ thuật] Đọc ĐỦ các lần gọi công cụ của MỘT lượt (để bộ dựng file của B in phần đầu file đúng nội dung
câu vừa hỏi). Nguồn: Postgres của Capitol — node_result.output_data → metadata → message_history (phần "tool-call" có tham số, phần "tool-return" có kết quả).
Khác may_chu.cong_cu_da_goi (cắt 400 ký tự để HIỆN): ở đây KHÔNG cắt — vượt chốt cỡ thì trả danh sách rỗng kèm lý do (không đưa dữ liệu thiếu vào file).
Dữ liệu này do hộp ab-worker ghi (coi là có thể bị sửa) ⇒ bộ dựng của B GỌI LẠI công cụ ở hộp KPI bằng đúng tham số rồi so; module này chỉ CHUYỂN, không bảo chứng.
  doc(phien, sql) ⇒ ([{"ten", "tham_so": dict, "ket_qua": dict | list | chữ}], lý do rỗng | None)   — sql = xem_workflow.sql; phien đã qua kiem_ma (UUID)."""
from __future__ import annotations

import json
import re

_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TEN = re.compile(r"[a-z][a-z0-9_]{2,60}")
NOI_BO = {"final_result", "report_input_issue"}          # công cụ nội bộ của khung agent — không phải công cụ KPI
SO_LAN_TOI_DA, CO_TOI_DA = 20, 1_500_000                 # bộ dựng của B nhận ≤ 20 mục (quá ⇒ coi sai khuôn, không xuất) và chỉ in 6 lần tra đầu; tổng ≤ 1,5 MB JSON (gói chốt 2 MB)


def cau_sql(phien: str) -> str:
    if not _MA.fullmatch(str(phien)):
        raise ValueError("mã phiên không đúng khuôn")
    return f"""select coalesce(json_agg(json_build_object('node', n.node_instance_id, 'at', m->>'timestamp',
            'parts', (select coalesce(json_agg(json_build_object('k', pp->>'part_kind', 'tool', pp->>'tool_name', 'id', pp->>'tool_call_id', 'args', pp->'args', 'content', pp->'content')), '[]')
                      from jsonb_array_elements(m->'parts') pp where pp->>'part_kind' in ('tool-call', 'tool-return', 'retry-prompt'))
        ) order by m->>'timestamp'), '[]')
        from node_result nr join node n on n.id=nr.node_id join workflow_run r on r.id=nr.run_id,
             jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) m
        where r.session_id='{phien}'"""


def _json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def doc(phien: str, sql) -> tuple[list, str | None]:
    try:
        ds = sql(cau_sql(phien)) or []
    except Exception as e:  # noqa: BLE001 — không đọc được ⇒ file vẫn xuất phần bảng đội, có nói lý do
        return [], f"không đọc được danh sách công cụ của lượt ({type(e).__name__})"
    goi, tra, hong = {}, {}, set()
    for msg in ds if isinstance(ds, list) else []:
        for p in (msg or {}).get("parts") or []:
            khoa = (msg.get("node"), p.get("id"))
            if p.get("k") == "tool-call":
                goi[khoa] = p
            elif p.get("k") == "tool-return":
                tra[khoa] = p.get("content")
            elif p.get("k") == "retry-prompt":
                hong.add(khoa)          # lần gọi bị trả về bắt gọi lại (tham số sai…) — không phải kết quả
    ra = []
    for khoa, g in goi.items():         # dict giữ thứ tự chèn = thứ tự gọi (SQL đã xếp theo giờ)
        ten = g.get("tool")
        ts = _json(g.get("args"))
        kq = _json(tra.get(khoa))
        if (khoa in hong or khoa not in tra or not isinstance(ten, str) or not _TEN.fullmatch(ten) or ten in NOI_BO or not isinstance(ts, dict)
                or not isinstance(kq, (dict, list))):   # bộ dựng của B chỉ nhận kết quả dạng dict / list (kết quả là chữ trơn ⇒ bỏ)
            continue
        ra.append({"ten": ten, "tham_so": ts, "ket_qua": kq})
    ly_do = None
    if len(ra) > SO_LAN_TOI_DA:   # giữ SO_LAN_TOI_DA lần ĐẦU (theo thứ tự gọi) và NÓI RA — bộ dựng ghi dòng chú này vào câu báo
        ly_do = f"lượt này gọi {len(ra)} lần công cụ, chỉ {SO_LAN_TOI_DA} lần đầu được đưa sang bộ dựng file"
        ra = ra[:SO_LAN_TOI_DA]
    co = len(json.dumps(ra, ensure_ascii=False).encode())
    if co > CO_TOI_DA:
        return [], f"kết quả công cụ của lượt lớn {co // 1024} KB, quá mức đưa vào file ({CO_TOI_DA // 1024} KB)"
    return ra, ly_do
