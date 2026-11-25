"""M3 — 20 câu hồi quy qua workflow KPI Capitol (Agent luna + 10 công cụ kpi_* ⇒ Rào chắn code ⇒ Người duyệt ⇒ Ô ra) — B 10/11/2026, đã giao.
TỐN TIỀN (luna, ~0,003 / câu ⇒ ~0,06). Bộ câu: mekongai kpi_agent/data/BO_CAU_AGENT_C.jsonl (nhóm kỹ thuật chốt, sha12 d4a17ba26f88 — ĐỌC TẠI CHỖ).
Backend phải ở CHE_DO=m3 (Tracking, cutoff 2026-10-20) — script kiểm /api/meta. Mỗi câu 1 lượt RIÊNG (chạy từ đầu, run_id riêng); Người duyệt 5 s
(M3 đo câu trả lời + Rào chắn, không đo duyệt) ⇒ hết giờ dừng, không tự duyệt.
Ghi thô (DỮ LIỆU THẬT đã che, Git bỏ qua): ket_qua/10_11/kpi/m3/<id>.json — câu trả lời Agent, lần gọi công cụ (tên + tham số), số trong kết quả công cụ,
Rào chắn chạy / xong / hỏng + lý do (Temporal), tiền. Chấm: cham_m3.py (0 đồng). Đã có <id>.json ⇒ bỏ qua câu đó (chạy tiếp được sau khi dừng).
Chạy (User gõ — nhóm kỹ thuật bị auto mode chặn lượt AI):
  ! cd ~/docker-images-src && DA_DUYET=co GOI_AI=co BUOI_S5=10_11/s5 TRAN_USD=0.20 timeout 3000 python3 -I chay_thu/kpi_capitol/chay_m3.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DAY = Path(__file__).resolve().parent
sys.path[:0] = [str(DAY), str(DAY.parent), str(DAY.parent / "workflow_mau")]
import workflow_kpi as W  # noqa: E402
import temporal_luot as T  # noqa: E402
import chay_mau as CM  # noqa: E402
from sandbox.chay_s5 import TRAN, da_tieu, ghi_tien, tien_luot  # noqa: E402

BO = Path(os.environ.get("KPI_TEST_QUESTIONS_FILE", ""))
SHA_BO = "d4a17ba26f88"
# [ghi chú] bộ câu khác (vd M3b GIỮ KÍN của D): M3_BO=<.jsonl> + M3_BO_SHA16=<16 hex> BẮT BUỘC đi cùng; script KHÔNG in câu hỏi.
if os.environ.get("M3_BO") or os.environ.get("M3_BO_SHA16"):
    if not (os.environ.get("M3_BO") and re.fullmatch(r"[0-9a-f]{16}", os.environ.get("M3_BO_SHA16", ""))):
        raise SystemExit("M3_BO và M3_BO_SHA16 (16 hex) phải đặt cùng nhau")
    if os.environ.get("M3_RA", "m3") == "m3":
        raise SystemExit("bộ câu khác ⇒ M3_RA phải khác m3")
    BO, SHA_BO = Path(os.environ["M3_BO"]), os.environ["M3_BO_SHA16"]
# [ghi chú] lượt chẩn đoán: M3_CAU="7,8,9" chỉ chạy các câu đó; M3_RA=<tên thư mục> (chữ / số / _) — mặc định "m3" như bản 1.
# Có M3_CAU thì BẮT BUỘC M3_RA khác "m3" (không trộn vào bản ghi M3 chính thức).
_RA_TEN = os.environ.get("M3_RA", "m3")
if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", _RA_TEN) or (os.environ.get("M3_CAU") and _RA_TEN == "m3"):
    raise SystemExit("M3_RA sai dạng, hoặc M3_CAU có mà M3_RA vẫn là m3")
# [ghi chú] thư mục ngày lấy từ BUOI_S5 như chay_kpi.py (không lẫn với 10_11/kpi/m3 — bản cũ có đủ 20 file ⇒ "dich.exists" bỏ qua hết)
_NGAY = (os.environ.get("BUOI_S5") or "").split("/")[0]
if _NGAY and not re.fullmatch(r"\d{2}_\d{2}", _NGAY):
    raise SystemExit(f"DỪNG: BUOI_S5 phải dạng dd_mm/s5 (nhận '{_NGAY}')")
RA = CM.KQ / (_NGAY or "10_11") / "kpi" / _RA_TEN
HOP = "capitol-ab-worker-temporal"
TRAN_CAU = 0.01   # chỉ còn là NGƯỠNG BÁO BẤT THƯỜNG sau câu (tiền thật 1 câu > 0,01 ⇒ dừng, báo A); giữ chỗ trước câu dùng mức tối đa (chay_kpi.du_tien)


def meta() -> dict:
    r = subprocess.run(["docker", "exec", HOP, "python", "-I", "-c",
                        "import json,urllib.request as u;m=json.load(u.urlopen('http://kpi-backend:8110/api/meta',timeout=10));"
                        "print(json.dumps({k:m.get(k) for k in ('period','cutoff','rule_version','status_label_for_model','employee_ids','workflow_version')}))"],
                       capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout or "{}")


def phien_ban() -> dict:
    """[ghi chú] ghi đúng bản đang đo: workflow (máy), Rào chắn + công cụ Agent (trong hộp), mô tả công cụ + kiem_so (backend:
    sha /api/tools; ảnh chụp mekongai đang gắn vào hộp kpi-backend — tên thư mục chứa commit)."""
    def sha_hop(duong: str) -> str:
        return subprocess.run(["docker", "exec", HOP, "sha256sum", duong], capture_output=True, text=True, timeout=30).stdout[:16]
    tools = subprocess.run(["docker", "exec", HOP, "python", "-I", "-c",
                            "import json,hashlib,urllib.request as u;t=json.load(u.urlopen('http://kpi-backend:8110/api/tools',timeout=10));"
                            "print(hashlib.sha256(json.dumps(t,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16])"],
                           capture_output=True, text=True, timeout=60).stdout.strip()
    gan = subprocess.run(["docker", "inspect", "-f", "{{range .Mounts}}{{.Source}} {{end}}", "capitol-kpi-backend"],
                         capture_output=True, text=True, timeout=30).stdout.split()
    return {"workflow_kpi_py": hashlib.sha256((DAY / "workflow_kpi.py").read_bytes()).hexdigest()[:16],
            "rao_node_py_hop": sha_hop("/srv/src/nodes/governance/guardrails/node.py"),
            "native_tools_py_hop": sha_hop("/srv/src/nodes/agent/native_tools.py"),
            "api_tools16": tools, "anh_chup_backend": sorted({"/".join(g.split("/")[:3]) for g in gan if g.startswith("/tmp/")})}


def sql_json(sql: str):
    return json.loads(CM.psql(sql) or "null")


def main() -> int:
    if os.environ.get("DA_DUYET") != "co" or os.environ.get("GOI_AI") != "co":
        print("Cần DA_DUYET=co GOI_AI=co")
        return 1
    if not _NGAY:
        print("DỪNG: cần BUOI_S5=<dd_mm>/s5 (sổ tiền + thư mục kết quả theo ngày)")
        return 1
    from chay_kpi import du_tien   # [ghi chú] giữ chỗ theo mức tối đa — cùng hàm với 8 câu
    b = BO.read_bytes()
    if hashlib.sha256(b).hexdigest()[:len(SHA_BO)] != SHA_BO:
        print(f"DỪNG: bộ câu khác sha {SHA_BO}")
        return 1
    cau = [json.loads(x) for x in b.decode("utf-8").splitlines() if x.strip()]
    assert len(cau) == 20 or (os.environ.get("M3_BO") and len(cau) >= 1)
    if os.environ.get("M3_CAU"):
        chon = {int(x) for x in os.environ["M3_CAU"].split(",")}
        cau = [q for q in cau if q["id"] in chon]
    m = meta()
    if m.get("cutoff") != "2026-10-20" or not str(m.get("workflow_version", "")).startswith("kpi_thuong_nvbh@") or len(m.get("employee_ids") or []) != 14:
        print(f"DỪNG: backend không ở cấu hình M3 (cutoff {m.get('cutoff')}, {m.get('workflow_version')}) — CHE_DO=m3 bash chay_thu/bat_kpi_backend_raw.sh")
        return 1
    org = (CM.KQ / "org_id.txt").read_text().strip()
    _, catalog = CM.goi("POST", f"/nodes/{org}/list-nodes", {})
    W.kiem_danh_muc(CM.tim_khoi, catalog)
    y, mth, d = m["cutoff"].split("-")
    RA.mkdir(parents=True, exist_ok=True)
    pb = phien_ban()
    (RA / "phien_ban.json").write_text(json.dumps(pb, ensure_ascii=False, indent=1))
    print("phiên bản:", json.dumps(pb, ensure_ascii=False))
    for q in cau:
        dich = RA / f"{q['id']:02d}.json"
        if dich.exists():
            continue
        v = CM.Ve(catalog)
        k = W.dung(v, q["question"], ky=m["period"], ngay_chot=f"{d}/{mth}/{y}", ban_luat=m.get("rule_version"),
                   trang_thai=m.get("status_label_for_model"), duyet_giay=5, co_duyet=os.environ.get("CO_DUYET", "co") == "co", nhan_vien=m.get("employee_ids"))
        kiem = W.kiem_payload(v.nodes, v.edges)
        if not du_tien(v.nodes):
            print(f"DỪNG trước câu {q['id']}")
            return 1
        kq = CM.luu_va_chay(org, v, f"KPI M3 câu {q['id']}")
        kq.pop("_hang", None)
        s = kq.get("session")
        if not s:
            print(f"câu {q['id']}: LƯU / CHẠY HỎNG {str(kq)[:200]}")
            return 1
        t = tien_luot(s)
        ghi_tien(f"kpi_M3_{q['id']:02d}", t["usd"], {"session": s, "ghi_chu": "M3 20 câu, chay_m3.py"})
        if kq.get("status") in ("", None, "RUNNING", "QUEUED", "PAUSED", "paused"):   # [ghi chú] như chay_kpi
            print(f"DỪNG LOẠT: câu {q['id']} lượt {s} còn '{kq.get('status')}' sau giờ chờ ⇒ tiền tiêu sau có thể chưa vào sổ — báo A")
            return 2
        tra_loi = CM.psql("select coalesce(string_agg(nr.output_data->>'data', ' '), '') from node_result nr join workflow_run r on r.id=nr.run_id "
                          f"where r.session_id='{s}' and nr.output_data::jsonb->'metadata' ? 'message_history'")
        goi = sql_json("select coalesce(json_agg(json_build_object('ten', pp->>'tool_name', 'args', pp->'args')), '[]') from node_result nr "
                       "join workflow_run r on r.id=nr.run_id, jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) mm, "
                       f"jsonb_array_elements(coalesce(mm->'parts', '[]'::jsonb)) pp where r.session_id='{s}' and pp->>'part_kind'='tool-call'")
        ve = CM.psql("select coalesce(string_agg(pp->>'content', ' '), '') from node_result nr join workflow_run r on r.id=nr.run_id, "
                     "jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) mm, "
                     f"jsonb_array_elements(coalesce(mm->'parts', '[]'::jsonb)) pp where r.session_id='{s}' and pp->>'part_kind'='tool-return'")
        tl = T.lich_su(s, k)
        out = {"id": q["id"], "session": s, "status": kq.get("status"), "kiem_payload": kiem, "tra_loi": tra_loi, "cong_cu": goi,
               "chu_cong_cu": ve, "rao": {"chay": tl["len_lich_theo_khoi"]["rao"], "xong": tl["xong"]["rao"], "hong": tl["hong"]["rao"]},
               "temporal_ly_do": tl.get("ly_do_loi"), "tien": round(t["usd"], 6), "meta": {x: m.get(x) for x in ("cutoff", "workflow_version", "rule_version")},
               "sha_bo12": SHA_BO, "phien_ban": pb}
        dich.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        print(f"câu {q['id']:02d}: {out['status']} | Rào xong {out['rao']['xong']} hỏng {out['rao']['hong']} | công cụ {len(goi or [])} | {t['usd']:.5f} USD")
        if t["usd"] > TRAN_CAU:
            print(f"CẢNH BÁO: câu {q['id']} {t['usd']:.5f} > {TRAN_CAU} — DỪNG, báo A")
            return 1
    print(f"== xong; luna sổ {da_tieu():.5f} / {TRAN}. Chấm: python3 -I chay_thu/kpi_capitol/cham_m3.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
