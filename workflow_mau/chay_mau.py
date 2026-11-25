"""Chạy workflow MẪU của Capitol (workflow-builder-agent, bản 03/2026) trên bộ của mình — 09/11/2026, nhóm kỹ thuật (đã giao, người vận hành 09/11).
Cách làm + dự đoán ghi TRƯỚC: chay_thu/workflow_mau/CHAY_MAU.md.
Chạy (từ ~/docker-images-src):
    BUOI_S5=09_11_sang/s5 TRAN_USD=0.08 DA_DUYET=co python3 -I chay_thu/workflow_mau/chay_mau.py            # cả 3 mẫu
    ... MAU=earnings_call_summarizer.json ...                                                                # 1 mẫu
Kết quả: chay_thu/ket_qua/<BUOI_MAU>/<mẫu>.json + <mẫu>.md (chữ Markdown ra). Mặc định BUOI_MAU=09_11_sang/workflow_mau. Đã có ⇒ bỏ qua mẫu đó.
Tiền: sổ chay_s5 (BUOI_S5); dừng trước mẫu nếu đã tiêu ≥ TRAN; dừng các mẫu còn lại nếu một mẫu > TRAN_MAU (0,005).
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from buoc4b_gui_workflow_thu import KQ, goi, tim_khoi
from buoc6b_chay_lai_tu_khoi import psql
from buoc15_workflow_nhieu_khoi import Ve
from sandbox.chay_s5 import CHUNG, SO, TRAN, da_tieu, du_tien_giu, ghi_tien, tien_luot
from giao_dien.muc_toi_da import MUC_TOI_DA, toi_da   # [ghi chú] giữ chỗ theo mức tối đa của từng mẫu trước khi chạy

GOC = Path(__file__).resolve().parents[2]
MAU_DIR = Path(os.environ.get("CAPITOL_SAMPLE_WF_DIR", ""))
BUOI = KQ / os.environ.get("BUOI_MAU", "09_11_sang/workflow_mau")
TRAN_MAU = float(os.environ.get("TRAN_MAU", "0.005"))
CHON = ["earnings_call_summarizer.json", "used_cars_for_teenagers_research.json", "pancake_research_workflow.json"]
CONG_CU_NGOAI = {"EXA_SEARCH", "EXA_GET_CONTENTS_ACTION", "SERPAPI_SEARCH", "TAVILY_SEARCH"}   # nối bằng dây port_to_port vào cổng Tools
AGENT = dict(CHUNG, max_tokens=2000)   # CHUNG: model gpt-6-luna, temperature None (vá bỏ temperature), timeout 180, retries 1

# Bản ghi cuộc họp HƯ CẤU (công ty bịa, số bịa) cho mẫu Earnings — mẫu gốc chỉ có chữ giữ chỗ "Paste your earnings call transcript here..."
BAN_GHI = """[FICTIONAL TEST TRANSCRIPT — Mekong Demo Foods Ltd., Q3 2026 earnings call. All names and numbers are invented.]
CEO (An Tran): Thank you for joining. Q3 revenue was 412 million USD, up 9% year over year, driven by ready-meal exports to Japan.
Gross margin was 31.2%, down from 32.0% last year because rice input costs rose about 6%.
CFO (Binh Le): Operating income was 58 million USD. Net income was 39 million USD, or 0.48 USD per share. Free cash flow was 27 million USD.
We ended the quarter with 120 million USD in cash and 210 million USD of debt. We keep full-year revenue guidance at 1.60 to 1.65 billion USD,
but we lower full-year gross margin guidance from 32% to about 31%.
CEO: Risks: a typhoon season that could disrupt the Can Tho plant, and a possible new import tariff in one export market. We will open a second
cold-storage site in Q1 2027 for about 15 million USD.
Analyst (Q): Will the margin recover next year? CFO (A): We expect partial recovery if rice prices stabilize, but we are not giving 2027 guidance yet."""


# [ghi chú] mẫu digital_humanities_analytics: Ô nhập gốc là chữ giữ chỗ ⇒ đoạn văn HƯ CẤU ngắn (tự viết, không bản quyền)
VAN_BAN = """[FICTIONAL TEST TEXT — invented for testing, no real people.] The river town of Ben Lac kept two ledgers in 1890: one for rice, one for
letters. Merchants wrote of floods and prices; mothers wrote of sons who left for the city. In the rice ledger, the word "flood" appears in nine
entries and "price" in fourteen. In the letters, "home" appears eleven times, "city" seven times, and "return" five times. By 1895 the letters
grow shorter and the rice entries grow longer, as if the town traded its words for grain."""



# [ghi chú] mẫu đồ thị gốc tra cứu NGƯỜI THẬT (lãnh đạo quân đội) ⇒ đổi đề bài + lời dặn 2 Agent sang chuỗi cung ứng HƯ CẤU,
# tắt tìm web (không có gì để tra). Giữ nguyên KHUÔN của Capitol: Ô nhập ⇒ Agent nghiên cứu ⇒ Agent phân tích ⇒ Đồ thị + Markdown.
TRUNG_TINH = {
    "text_input": "FICTIONAL supply chain (all companies invented): Lua Vang Rice Co. buys paddy from Song Hau Farms and Cuu Long Coop; "
                  "Lua Vang sells milled rice to Mekong Foods and to Delta Mart; Mekong Foods also buys from Cuu Long Coop; "
                  "Tan Phu Logistics ships for Lua Vang and Mekong Foods; Delta Mart and Mekong Foods share the same owner, An Group.",
    "agent": ["You are a supply-chain researcher. Using ONLY the fictional description given by the user (do not search, do not add real "
              "companies), list every company and every relationship (buyer, supplier, shipper, owner) as a clear structured list.",
              "You are a network analyst. Turn the structured list into a node list (companies) and an edge list (relationship type) for a "
              "network graph, then give a 3-sentence summary of which company is most central and why."],
}


def doc_mau(ten):
    p = next((x / ten for x in (MAU_DIR, MAU_DIR.parent) if (x / ten).exists()), MAU_DIR / ten)   # vài mẫu nằm ở srv/ (mr_bean_search_report)
    d = json.loads(p.read_text(encoding="utf-8"))
    nodes = {n["id"]: n["data"]["struct"] for n in d["nodes"]}
    return d, nodes


def dung_lai(ten, catalog):
    """Dựng lại workflow từ danh mục MỚI, chỉ chép giá trị ô theo field_id. Trả (Ve, ghi chép chuyển đổi)."""
    d, nodes = doc_mau(ten)
    v, moi, ghi_chep = Ve(catalog), {}, {"o_bi_bo": [], "khoi_bo": [], "day_bo": 0, "agent_web": []}
    for nid, s in nodes.items():
        loai = s["node_id"]
        if loai in CONG_CU_NGOAI:
            ghi_chep["khoi_bo"].append(loai)
            continue
        cu = {p["field_id"]: p.get("value") for p in s.get("params", []) if p.get("value") not in (None, "", [], {})}
        mau_moi = tim_khoi(catalog, loai)   # danh mục là dict lồng nhiều tầng (success / total_nodes / catalog …)
        o_moi = {p["field_id"] for p in (mau_moi or {}).get("params", [])} if mau_moi else None
        if o_moi is not None:
            ghi_chep["o_bi_bo"] += [f"{loai}.{k}" for k in cu if k not in o_moi]
            cu = {k: x for k, x in cu.items() if k in o_moi}
        if loai == "agent_node":
            cu.update(AGENT)
        if loai == "text_input_node" and ten.startswith("earnings_call_summarizer"):
            cu["text_input"] = BAN_GHI
        if loai == "text_input_node" and ten.startswith("comprehensive_environmental_impact_analysis") and cu.get("text_input"):
            # mẫu gốc chỉ có khung "[Enter …]" ⇒ thêm 1 dòng giá trị HƯ CẤU ở đầu, giữ nguyên khung của họ
            cu["text_input"] = ("FICTIONAL TEST VALUES (invented project, use these to fill the brackets): mixed-use 6-storey building, "
                                "12,000 m2, on a 1.5 ha former rice-processing brownfield beside a canal in Can Tho, Vietnam; tropical monsoon "
                                "climate, flood-prone; nearby wetland; local zoning allows mixed-use.\n\n" + cu["text_input"])
        if loai == "text_input_node" and ten.startswith("digital_humanities_analytics"):
            cu["text_input"] = VAN_BAN
        if loai in ("data_analysis_agent_node", "network_graph_output_node", "d3_data_visualization_node", "xlsx_chat_node") or (
                "model" in cu and cu["model"] != "gpt-6-luna") or (o_moi is not None and "model" in o_moi):   # [ghi chú] ô model TRỐNG ⇒ khối lấy mặc định danh mục (claude-opus-5)
            cu["model"] = "gpt-6-luna"   # [ghi chú] hộp phiên chỉ chạy gpt-6-luna; [ghi chú] MỌI ô model ⇒ luna (mẫu PowerPoint đặt claude-opus-5)
        if ten.startswith("taiwan_military_leadership_network"):
            if loai == "text_input_node":
                cu["text_input"] = TRUNG_TINH["text_input"]
            if loai == "agent_node":
                cu["system_prompt"] = TRUNG_TINH["agent"][min(ghi_chep.setdefault("_agent_thu", 0), 1)]
                ghi_chep["_agent_thu"] += 1
        moi[nid] = v.khoi(loai, s.get("name") or loai, 0, 0, cu)
    for e in d["edges"]:
        nguon, dich = e["source"], e["target"]
        if e.get("edge_type") == "port_to_port" or nodes[nguon]["node_id"] in CONG_CU_NGOAI:
            ghi_chep["day_bo"] += 1
            if nodes[dich]["node_id"] == "agent_node":   # công cụ EXA vào Agent ⇒ bật web_search Serper của mình trên đúng Agent đó
                for p in v._struct(moi[dich])["params"]:
                    if p["field_id"] == "tool_web_search":
                        p["value"] = True
                ghi_chep["agent_web"].append(moi[dich])
            continue
        v.noi(moi[nguon], moi[dich], e["target_param_id"])
    ghi_chep["agent_web"] = len(set(ghi_chep["agent_web"]))
    if ten.startswith("taiwan_military_leadership_network"):   # đề hư cấu ⇒ không tra web
        for n in v.nodes:
            for p in n["data"]["struct"]["params"]:
                if p["field_id"] == "tool_web_search":
                    p["value"] = False
        ghi_chep["agent_web"] = 0
        ghi_chep["de_bai_doi"] = "chuỗi cung ứng HƯ CẤU (TRUNG_TINH), tắt web"
    ghi_chep.pop("_agent_thu", None)
    return v, ghi_chep, d.get("name")


def tien_skill(tu: str) -> dict:
    import re
    import subprocess
    vao = ra = n = 0
    for h in ("capitol-ab-worker-temporal", "capitol-ab-worker"):
        r = subprocess.run(["docker", "logs", "--since", tu, h], capture_output=True, text=True, timeout=60)
        for d in (r.stdout + r.stderr).splitlines():
            if "[CACHE_USAGE] node_type=skills:" in d:
                m1, m2 = re.search(r"total_input=(\d+)", d), re.search(r" output=(\d+)", d)
                if m1 and m2:
                    vao, ra, n = vao + int(m1.group(1)), ra + int(m2.group(1)), n + 1
    return {"dong": n, "token_vao": vao, "token_ra": ra, "usd": (vao * 0.1 + ra * 0.5) / 1_000_000}


KHOI_HOP_PHIEN = {"pptx_chat_node", "docx_chat_node", "xlsx_chat_node", "flexible_code_interpreter_node", "skill_code_interpreter_node"}


def tien_hop_phien(nodes: list[dict], status: str | None, do_duoc_usd: float) -> float:
    """[ghi chú] Tiền phần KHỐI CHẠY TRONG HỘP PHIÊN (PowerPoint / Word / Excel-chat / chạy code) của một lượt.
    Các khối này không ghi message_history; tiền đo từ dòng log [CACHE_USAGE] skills (tien_skill) — nhưng khi khối HỎNG (vd chạm trần token) thì KHÔNG
    có dòng log ⇒ sổ thiếu (12/11 15:03: khối PowerPoint dùng ~137k token vào, script chỉ thấy 0,00035). Luật: lượt SUCCESS ⇒ ghi số đo được; lượt KHÔNG
    SUCCESS mà workflow có khối loại này ⇒ ghi max(số đo được, TỔNG mức tối đa của các khối đó) — thà ghi dư ở lượt hỏng còn hơn sổ thiếu.
    Workflow không có khối loại này ⇒ trả đúng số đo được (thường 0)."""
    hop = [n["data"]["struct"]["node_id"] for n in nodes if n["data"]["struct"]["node_id"] in KHOI_HOP_PHIEN]
    if not hop or status == "SUCCESS":
        return do_duoc_usd
    return max(do_duoc_usd, round(sum(MUC_TOI_DA[x] for x in hop), 4))


def luu_va_chay(org, v, ten_mau):
    # [ghi chú] workflow do SCRIPT ĐO tự lưu mang tiền tố "ĐO — " (trước: "Mẫu Capitol - …" ⇒ Trợ lý chat trên trang chọn nhầm làm mẫu;
    # nhóm mẫu cho người dùng tên "Mẫu Capitol <số> — …" do tao_workflow_mau_chung.py lưu).
    pl = {"id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "name": f"ĐO — {ten_mau}", "description": "chay_thu B 09/11: mẫu workflow-builder-agent",
          "nodes": v.nodes, "edges": v.edges, "metadata": {}, "global_context": {},
          "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False}
    c, r = goi("POST", f"/api/v1/orgs/{org}/workflows", pl)
    wf = ((r.get("workflow") or {}).get("id") or r.get("workflow_id") or r.get("id")) if isinstance(r, dict) else None
    if not wf:
        return {"luu_http": c, "luu": str(r)[:1500]}
    _, ses = goi("GET", "/session/create-session")
    s = ses["session_id"]
    t0 = time.time()
    c2, r2 = goi("POST", f"/workflow/{org}/{wf}/{s}/run-workflow", dict(pl, id=wf, session_id=s))
    if c2 == 403:
        # [ghi chú] 2 lần trong ngày (09:43, 16:11) run-workflow trả 403 NGAY sau khi lưu workflow mới, không tạo lượt, chạy lại thì
        # được. Nguyên nhân [D, suy luận khớp dữ kiện]: chạy đua commit — lúc lưu, quyền chủ workflow mới flush + ghi log "Granted" (services/workflow_access/
        # workflow_access.py:638–641), commit nằm sau `yield` của phiên DB (api/v1/deps.py:132–136); run-workflow kiểm quyền bằng phiên DB khác trước thân hàm
        # (api/routers/workflow.py:537 ⇒ api/v1/deps.py:669–674) ⇒ gọi chạy trước khi commit xong thì 403.
        # ⇒ thử lại ĐÚNG MỘT lần sau 2 giây. [D đính chính 16:2x] còn 9 chỗ 403 khác trong api/v1/handlers/run.py CHƯA đọc ⇒ chỉ gửi lại khi Postgres CHƯA có
        # workflow_run nào của đúng phiên này; đã có dòng ⇒ coi lượt ĐÃ khởi động, không gửi lại (tránh chạy đôi), đi tiếp nhánh chờ + ghi tiền.
        print(f"run-workflow HTTP 403 ngay sau khi lưu: {str(r2)[:200]}")
        time.sleep(2)
        da_co = psql(f"select count(*) from workflow_run where session_id='{s}'")
        if str(da_co).strip() == "0":
            c2, r2 = goi("POST", f"/workflow/{org}/{wf}/{s}/run-workflow", dict(pl, id=wf, session_id=s))
            print(f"  chưa có lượt nào của phiên này ⇒ thử lại MỘT lần: HTTP {c2}")
        else:
            c2 = 202
            print(f"  Postgres đã có {str(da_co).strip() or '?'} lượt của phiên này ⇒ KHÔNG gửi lại; coi lượt đã khởi động, chờ như thường")
    if c2 not in (200, 202):   # [ghi chú] 09:43 12/11 run-workflow trả 403 ⇒ không có lượt nào nhưng vòng dưới vẫn chờ ~5 phút. Dừng ngay;
        # status để "" như khi hết giờ chờ ⇒ người gọi (chay_kpi / chay_m3) dừng loạt y như cũ, chỉ nhanh hơn. Không lượt ⇒ 0 đồng.
        print(f"run-workflow HTTP {c2} — KHÔNG có lượt nào được tạo: {str(r2)[:300]}")
        return {"workflow_id": wf, "luu_http": c, "chay_http": c2, "session": s, "status": "", "giay_dong_ho_may": round(time.time() - t0),
                "loi_luot": f"run-workflow HTTP {c2}: {str(r2)[:300]}", "khoi": [], "_hang": []}
    st = ""
    for _ in range(150):
        st = psql(f"select status from workflow_run where session_id='{s}'")
        if st and st not in ("RUNNING", "QUEUED", "PAUSED", "paused"):
            break
        time.sleep(2)
    time.sleep(3)
    # chữ ra: dòng field_id của khối Markdown (node_type / tên hiển thị trong DB đều là "custom" — chỉ field_id phân biệt được)
    hang = json.loads(psql(
        "select coalesce(json_agg(json_build_object('status', nr.status, 'field_id', nr.output_data->>'field_id', "
        "'chu', nr.output_data->>'data', 'loi', left(coalesce(nr.error_message, ''), 400)) order by nr.created_at), '[]') "
        f"from node_result nr join workflow_run r on r.id = nr.run_id where r.session_id='{s}'") or "[]")
    return {"workflow_id": wf, "luu_http": c, "chay_http": c2, "session": s, "status": st, "giay_dong_ho_may": round(time.time() - t0),
            "loi_luot": psql(f"select left(coalesce(error_message,''),600) from workflow_run where session_id='{s}'"),
            "khoi": [{k: (x[k] if k != "chu" else len(x[k] or "")) for k in x} for x in hang], "_hang": hang}


def main() -> int:
    if os.environ.get("DA_DUYET") != "co":
        print("Chưa duyệt. Chạy: BUOI_S5=09_11_sang/s5 TRAN_USD=0.08 DA_DUYET=co python3 -I chay_thu/workflow_mau/chay_mau.py")
        return 1
    BUOI.mkdir(parents=True, exist_ok=True)
    SO.parent.mkdir(parents=True, exist_ok=True)   # ghi_tien không tự tạo thư mục sổ (bài học 09/11)
    org = (KQ / "org_id.txt").read_text().strip()
    _, catalog = goi("POST", f"/nodes/{org}/list-nodes", {})   # [ghi chú] danh mục HIỆN TẠI (bản 06/11 thiếu ô của khối Word ⇒ KeyError 'params')
    chon = [os.environ["MAU"]] if os.environ.get("MAU") else CHON
    for ten in chon:
        ra = BUOI / ten
        if ra.exists():
            print(f"{ten}: đã có kết quả — bỏ qua")
            continue
        if da_tieu() >= TRAN:
            print(f"DỪNG trước {ten}: đã tiêu {da_tieu():.5f} ≥ trần {TRAN}")
            return 1
        v, ghi_chep, ten_goc = dung_lai(ten, catalog)
        print(f"{ten} ({ten_goc}): {len(v.nodes)} khối, {len(v.edges)} dây; bỏ khối {ghi_chep['khoi_bo']}, bỏ {ghi_chep['day_bo']} dây; "
              f"Agent bật web {ghi_chep['agent_web']}; ô bị bỏ (lệch phiên bản) {ghi_chep['o_bi_bo']}")
        can, thieu = toi_da({"nodes": v.nodes})
        if thieu:
            print(f"DỪNG trước {ten}: khối gọi AI chưa có mức tối đa {thieu[:5]}")
            return 1
        if not du_tien_giu(can, f"mau_{ten[:-5]}"):   # [ghi chú] giữ THẬT trong bảng dùng chung; trả ở lần ghi_tien kế (chay_s5)
            return 1
        tu = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        kq = luu_va_chay(org, v, ten_goc)
        if not kq.get("session"):   # lưu / tạo lượt hỏng ⇒ chưa tốn tiền; ghi file .loi riêng để chạy lại không bị chặn
            (BUOI / (ten[:-5] + f".loi_{time.strftime('%H%M%S')}.json")).write_text(json.dumps(dict(kq, chuyen_doi=ghi_chep), ensure_ascii=False, indent=2))
            print(f"  ⇒ LƯU / CHẠY HỎNG: {str(kq)[:300]}")
            return 1
        t = tien_luot(kq["session"]) if kq.get("session") else {"usd": 0.0, "cong_cu": []}
        t["skill"] = tien_skill(tu)   # [ghi chú] khối skill (E2B ⇒ hộp phiên) không ghi message_history ⇒ đo dòng [CACHE_USAGE] skills:*
        t["hop_phien_usd"] = tien_hop_phien(v.nodes, kq.get("status"), t["skill"]["usd"])   # [ghi chú] lượt hỏng có khối hộp phiên ⇒ cận trên
        t["usd"] += t["hop_phien_usd"]
        if kq.get("session"):
            ghi_tien(f"mau_{ten[:-5]}", t["usd"], {"session": kq["session"], "ghi_chu": "workflow mẫu Capitol, chay_mau.py"})
        md = next((x["chu"] for x in kq.pop("_hang", []) if x.get("field_id") and "markdown" in x["field_id"] and x.get("chu")), None)
        if md:
            (BUOI / (ten[:-5] + ".md")).write_text(md, encoding="utf-8")
        kq.update(mau=ten, ten_goc=ten_goc, chuyen_doi=ghi_chep, tien=t, web_search=t["cong_cu"].count("web_search"),
                  co_markdown=bool(md), do_dai_markdown=len(md or ""))
        ra.write_text(json.dumps(kq, ensure_ascii=False, indent=2, default=str))
        print(f"  ⇒ {kq.get('status')} | web_search {kq['web_search']} lần | {t['usd']:.5f} USD | Markdown {kq['do_dai_markdown']} ký tự | lỗi: {kq.get('loi_luot','')[:150]!r}")
        if t["usd"] > TRAN_MAU:
            print(f"DỪNG các mẫu còn lại: mẫu này {t['usd']:.5f} > {TRAN_MAU} USD")
            return 1
    print(f"== đã tiêu (sổ {SO.parent.name}): {da_tieu():.5f} / {TRAN} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
