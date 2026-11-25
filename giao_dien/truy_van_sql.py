"""CÂU TRUY VẤN SQL một lượt đã chạy — cho màn Kết quả của workflow "Hỏi dữ liệu bằng lời" (đối chiếu kỹ thuật).
CHỈ ĐỌC: lấy từ bản ghi lần gọi công cụ `sql_query` của khối Agent (node_result ⇒ metadata ⇒ message_history trong PG của bộ chạy thử) — KHÔNG chạy lại
câu SQL nào, không chạm kho dữ liệu của công cụ. Trang hiện: câu SQL nguyên văn (chữ đơn cách, chỉ đọc) + bảng kết quả công cụ đã trả cho AI.
Mã lượt phải qua may_chu.kiem_ma (đúng khuôn UUID) TRƯỚC khi vào đây — câu SELECT dưới ghép chuỗi mã đó. Kết quả đi ra qua _json (xem.che che mẫu khoá).
Trần: 20 lần gọi / lượt, câu SQL 4.000 ký tự, kết quả 120.000 ký tự / lần (dài hơn ⇒ không dựng bảng, ghi rõ), 200 dòng × 40 cột, ô 300 ký tự.
"""
import json

TEN_CONG_CU = "sql_query"
TRAN_LAN, TRAN_SQL, TRAN_KQ, TRAN_DONG, TRAN_COT, TRAN_O = 20, 4000, 120_000, 200, 40, 300
NHAN = "Truy vấn tự do trên dữ liệu thử — KHÔNG phải số tính thưởng"


def _cau_sql(args) -> str:
    """args của lần gọi: chuỗi JSON {"cau_sql": "..."} (hoặc đã là dict). Không đọc được ⇒ trả nguyên chữ (vẫn chỉ để xem)."""
    d = args
    if isinstance(args, str):
        try:
            d = json.loads(args)
        except ValueError:
            return args[:TRAN_SQL]
    if isinstance(d, dict):
        v = d.get("cau_sql")
        return (v if isinstance(v, str) else json.dumps(d, ensure_ascii=False))[:TRAN_SQL]
    return str(d)[:TRAN_SQL]


def _o(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= TRAN_O else s[:TRAN_O] + "…"


def _ket_qua(content, dai: int) -> dict:
    if not content:
        return {"co": False, "ghi_chu": "Chưa có kết quả ghi lại cho lần gọi này."}
    if dai > TRAN_KQ:
        return {"co": False, "ghi_chu": f"Kết quả dài {dai:,} ký tự — quá cỡ để dựng bảng ở đây.".replace(",", ".")}
    try:
        d = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        return {"co": False, "ghi_chu": "Kết quả không ở dạng bảng (công cụ báo lại bằng chữ).", "chu": str(content)[:600]}
    if not isinstance(d, dict):
        return {"co": False, "ghi_chu": "Kết quả không ở dạng bảng.", "chu": str(d)[:600]}
    if d.get("ok") is not True:
        return {"co": False, "loi": str(d.get("error") or d.get("loai") or "Công cụ báo lỗi.")[:600]}
    cot = [str(c)[:80] for c in (d.get("cot") or [])][:TRAN_COT]
    tat_ca = [r for r in (d.get("dong") or []) if isinstance(r, list)]
    dong = [[_o(v) for v in r[:TRAN_COT]] for r in tat_ca[:TRAN_DONG]]
    return {"co": True, "cot": cot, "dong": dong, "so_dong": d.get("so_dong") if isinstance(d.get("so_dong"), int) else len(tat_ca),
            "da_cat": bool(d.get("da_cat")) or len(tat_ca) > TRAN_DONG, "ghi_chu": str(d.get("ghi_chu") or "")[:300]}


def doc(xem, phien: str) -> dict:
    """{"nhan": …, "lan": [{khoi, luc, sql, ket_qua{…}}]} — lan rỗng = lượt không gọi công cụ SQL."""
    ds = xem.sql(f"""select coalesce(json_agg(x), '[]') from (
          select json_build_object('node', n.node_instance_id, 'at', m->>'timestamp', 'k', pp->>'part_kind', 'id', pp->>'tool_call_id',
                   'args', left(coalesce(pp->>'args', ''), {TRAN_SQL * 2}), 'content', left(coalesce(pp->>'content', ''), {TRAN_KQ}),
                   'dai', length(coalesce(pp->>'content', ''))) x
          from node_result nr join node n on n.id = nr.node_id join workflow_run r on r.id = nr.run_id,
               jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) m,
               jsonb_array_elements(m->'parts') pp
          where r.session_id = '{phien}' and pp->>'tool_name' = '{TEN_CONG_CU}' and pp->>'part_kind' in ('tool-call', 'tool-return')
          order by m->>'timestamp' limit {TRAN_LAN * 2 + 2}) t""") or []
    goi, tra = {}, {}
    for p in ds:
        khoa = (p.get("node"), p.get("id"))
        if p.get("k") == "tool-call":
            goi.setdefault(khoa, p)
        else:
            tra[khoa] = p
    lan = []
    for khoa, g in list(goi.items())[:TRAN_LAN]:
        t = tra.get(khoa) or {}
        lan.append({"khoi": g.get("node"), "luc": g.get("at"), "sql": _cau_sql(g.get("args")), "ket_qua": _ket_qua(t.get("content"), int(t.get("dai") or 0))})
    return {"nhan": NHAN, "lan": lan, "con_nua": len(goi) > TRAN_LAN}
