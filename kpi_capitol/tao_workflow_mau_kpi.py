"""Lưu BỘ WORKFLOW MẪU KPI (khoi_dung_san.MAU) lên trang 18400 người vận hành bấm — B 12/11/2026, đã giao. 0 ĐỒNG: chỉ LƯU (POST), KHÔNG chạy lượt nào.
Mỗi mẫu = "Bộ KPI sẵn" (5 khối đã nối, có Người duyệt ⇒ duyệt là xuất Excel) + 1 câu hỏi điền sẵn; trước khi lưu phải qua
workflow_kpi.kiem_payload (luật nối) VÀ khoi_dung_san.kiem_khoi_dung_san (ô khoá = bản chuẩn, lời dặn = workflow_kpi.loi_dan()).
Cần: bộ chính + capitol-kpi-backend đang bật. Chạy sau mỗi lần có tổ chức thử mới hoặc đổi hộp / lời dặn; chạy lại KHÔNG đẻ bản trùng (cùng tên + cùng dấu ⇒ bỏ qua; LUU_LAI=co ⇒ lưu bất kể):
  cd ~/docker-images-src && timeout 180 python3 -I chay_thu/kpi_capitol/tao_workflow_mau_kpi.py
  HAU_TO=' v2' …   ⇒ thêm hậu tố vào tên (chỉ chữ / số)        CHI_KIEM=co …   ⇒ dựng + kiểm, KHÔNG lưu
  CHI_CHAT=co …    ⇒ chỉ 2 mẫu KHÔNG DUYỆT cho thẻ khung chat (khoi_dung_san.MAU_CHAT) — chạy sau mỗi lần hộp KPI đổi, như hai chế độ kia
Cũng ghi lại chay_thu/kpi_capitol/khoi_dung_san.json (file cho trang — không có chữ lời dặn)."""
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

# trường /api/meta (chay_m3.meta trả) đi vào DẤU của mẫu (đổi một trường ⇒ lưu bản mới): kỳ, ngày chốt, luật, trạng thái mà lời dặn chép.
# KHÔNG có data_version: mẫu để trống ô kpi_data_version (= bản lúc bật, workflow_kpi.py:163,168) nên đổi số liệu không làm mẫu cũ sai.
TRUONG_DAU = ("period", "cutoff", "rule_version", "status_label_for_model", "workflow_version")

# [B 20/11/2026 va_dau_mau_o_khoa — lỗi đã gặp 12:40 20/11: đổi một Ô KHOÁ của bản chuẩn (ô tự duyệt khi quá hạn) mà chạy thường thì script báo "đã có cùng dấu, bỏ qua" ⇒ mẫu cũ ở
# lại, phải nhớ đặt LUU_LAI=co] Dấu nay gồm thêm mã băm các Ô KHOÁ của bản chuẩn (tên ô khoá + giá trị cứng + ô dây + ô được sửa của từng khối dựng sẵn — đúng thứ
# khoi_dung_san.xuat_json ghi cho trang; lời dặn đã có mã băm riêng). ĐỂ KHÔNG đẻ 28 bản trùng ngay lần chạy đầu: khi mã băm ô khoá BẰNG mốc lúc viết bản vá
# (O_KHOA_MOC — đúng bản chuẩn mà các mẫu đang lưu mang) thì dấu tính Y NHƯ CŨ; khác mốc ⇒ dấu có thêm mã băm ⇒ khác mọi dấu cũ ⇒ lưu bản mới. Về lại đúng mốc ⇒ lại ra dấu cũ.
O_KHOA_MOC = "e1fedaedd16c1596"


def bam_o_khoa(khoi: list) -> str:
    """Mã băm 16 ký tự của phần Ô KHOÁ trong danh sách khối mà khoi_dung_san.xuat_json trả (ô "khoi"). Thuần — bộ thử gọi thẳng."""
    return hashlib.sha256(json.dumps({k["ma"]: {"khoi_goc": k["khoi_goc"], "o_khoa": k["o_khoa"], "gia_tri_cung": k["gia_tri_cung"], "o_day": k["o_day"], "sua_duoc": sorted(k["sua_duoc"])}
                                      for k in khoi}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def tinh_dau(mau: dict, loi_dan_sha16: str, meta: dict, khoi: list, phien_ban: str) -> str:
    """Dấu của MỘT mẫu. Thuần — bộ thử gọi thẳng."""
    goi = {"mau": mau, "phien_ban": phien_ban, "loi_dan": loi_dan_sha16, "meta": {k: meta.get(k) for k in TRUONG_DAU}}
    ok = bam_o_khoa(khoi)
    if ok != O_KHOA_MOC:
        goi["o_khoa"] = ok
    return hashlib.sha256(json.dumps(goi, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


DAY = Path(__file__).resolve().parent
sys.path[:0] = [str(DAY), str(DAY.parent), str(DAY.parent / "workflow_mau")]
import workflow_kpi as W  # noqa: E402
import chay_mau as CM  # noqa: E402
import chay_m3  # noqa: E402
import khoi_dung_san as K  # noqa: E402


def main() -> int:
    m = chay_m3.meta()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m.get("cutoff") or "")):
        print("DỪNG: kpi-backend chưa sẵn (meta thiếu cutoff) — bật hộp rồi chờ vài giây")
        return 1
    hau_to = os.environ.get("HAU_TO", "")
    if hau_to and not re.fullmatch(r" [0-9A-Za-z]{1,20}", hau_to):
        print("DỪNG: HAU_TO sai dạng (vd ' v2' — chỉ chữ / số)")
        return 1
    org = (CM.KQ / "org_id.txt").read_text().strip()
    _, catalog = CM.goi("POST", f"/nodes/{org}/list-nodes", {})
    W.kiem_danh_muc(CM.tim_khoi, catalog)
    chuan = K.khoi_chuan(CM.Ve, catalog, m)
    d = K.xuat_json(chuan, DAY / "khoi_dung_san.json")
    print(f"khoi_dung_san.json: phiên bản {d['phien_ban']}, lời dặn {d['loi_dan_sha16']}, {len(d['khoi'])} khối, {len(d['mau'])} mẫu; ô khoá {bam_o_khoa(d['khoi'])}"
          + ("" if bam_o_khoa(d["khoi"]) == O_KHOA_MOC else f" (KHÁC mốc {O_KHOA_MOC} ⇒ dấu mẫu đổi ⇒ sẽ lưu bản mới)"))
    loi = bo_qua = 0
    # [ghi chú] tổ chức giờ được GIỮ qua down / up (dung_lai seed lại tổ chức cũ) ⇒ chạy lại script không được đẻ bản
    # trùng. Bỏ qua khi đã có workflow CÙNG TÊN + CÙNG DẤU; khác dấu ⇒ lưu bản mới (bên đọc lấy bản mới nhất theo tên), KHÔNG xoá bản cũ (xoá cần User).
    # Dấu = băm mẫu (câu hỏi / nhánh) + phiên bản bộ + lời dặn + các trường meta mà lời dặn / luật lúc Chạy chép vào workflow: đổi hộp (bản mới,
    # m1 ⇄ m3, data_version) ⇒ dấu đổi ⇒ lưu lại — vì "KPI hỏi nhanh" bản cũ sẽ bị luật lúc Chạy từ chối. LUU_LAI=co ⇒ lưu bất kể dấu (cách cũ).
    c_ds, r_ds = CM.goi("GET", f"/api/v1/orgs/{org}/workflows")
    if c_ds != 200 or not isinstance(r_ds, dict):
        print(f"DỪNG: không đọc được danh sách workflow của tổ chức (HTTP {c_ds}) — không lưu gì để khỏi đẻ bản trùng")
        return 1
    da_co: dict[str, list] = {}
    for w in r_ds.get("workflows") or []:
        da_co.setdefault(w.get("name"), []).append(w)
    # [ghi chú] CHI_NHANH=co ⇒ chỉ (các) mẫu "KPI hỏi nhanh" (4 khối, không Giám sát duyệt — cho Trợ lý chat chạy); mặc định: 8 mẫu + hỏi nhanh
    ds = K.MAU_NHANH if os.environ.get("CHI_NHANH") == "co" else K.MAU + K.MAU_NHANH
    if os.environ.get("CHI_CHAT") == "co":   # [ghi chú] chỉ các mẫu KHÔNG DUYỆT cho thẻ khung chat (khoi_dung_san.MAU_CHAT) — chế độ riêng để số mẫu hai chế độ cũ không đổi
        ds = list(K.MAU_CHAT)
    if os.environ.get("CHI_HINH") == "co":   # [ghi chú] bộ mẫu KPI đa dạng (khoi_dung_san.MAU_HINH: trường hợp mới + hình nhiều nhánh); CHON=9,13 chọn theo số
        chon = {int(x) for x in os.environ.get("CHON", "").split(",") if x.strip()}
        ds = [x for x in K.MAU_HINH if not chon or x["so"] in chon]
    for mau in ds:
        if "nhanh" in mau:
            mau = dict(mau, co_duyet=any(n.get("co_duyet") for n in mau["nhanh"]), hinh=True)
        ten = mau["ten"] + hau_to
        if mau.get("hinh"):
            v, _ = K.tao_hinh(CM.Ve, catalog, mau, m)
        else:
            v, _ = K.tao_bo_san(CM.Ve, catalog, mau["cau_hoi"], m, co_duyet=mau.get("co_duyet", True))          # đã qua kiem_payload
        if not mau.get("co_duyet", True) and not mau.get("hinh"):   # không có bước duyệt ⇒ khối ra không được mang tên "Kết quả đã duyệt" (ô name không khoá)
            for n in v.nodes:
                if n["data"]["struct"].get("node_id") == "text_output_node":
                    n["data"]["struct"]["name"] = K.TEN_KET_QUA_NHANH
        kq = K.kiem_khoi_dung_san(v.nodes, chuan, m, v.edges)                    # ô khoá = bản chuẩn, lời dặn đúng — sai ⇒ ValueError, không lưu
        if os.environ.get("CHI_KIEM") == "co":
            print(f"{ten}: kiểm ĐẠT ({kq['so_khoi']} khối) — KHÔNG lưu (CHI_KIEM=co)")
            continue
        dau = tinh_dau(mau, kq["loi_dan_sha16"], m, d["khoi"], K.PHIEN_BAN)
        cu = da_co.get(ten) or []
        if os.environ.get("LUU_LAI") != "co":
            trung = [w for w in cu if f"[dấu {dau}]" in (w.get("description") or "")]
            if trung:
                print(f"{ten}: ĐÃ CÓ cùng dấu {dau} (workflow {trung[-1].get('id')}) — bỏ qua, không lưu bản trùng")
                bo_qua += 1
                continue
            for w in cu:
                print(f"  {ten}: bản cũ {w.get('id')} khác dấu — giữ nguyên (xoá cần User), lưu bản mới")
        pl = {"id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "name": ten,
              "description": f"Workflow mẫu KPI ({K.PHIEN_BAN}, lời dặn {kq['loi_dan_sha16']}) [dấu {dau}] — chỉ sửa câu ở khối Câu hỏi giám sát rồi bấm Chạy. "
                             + (mau.get("ghi_chu") or ("Bấm Duyệt ở khối Giám sát duyệt để nhận kết quả + file Excel." if mau.get("co_duyet", True) else "Kết quả là số tạm tính đã qua kiểm số bằng code, chưa có người duyệt.")),
              "nodes": v.nodes, "edges": v.edges, "metadata": {}, "global_context": {},
              "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False}
        c, r = CM.goi("POST", f"/api/v1/orgs/{org}/workflows", pl)
        wf = ((r.get("workflow") or {}).get("id") or r.get("workflow_id") or r.get("id")) if isinstance(r, dict) else None
        print(f"{ten}: HTTP {c} workflow {wf or 'KHÔNG LƯU ĐƯỢC ' + str(r)[:200]}")
        loi += not wf
    print(f"Xong: {len(ds) - loi} / {len(ds)} mẫu ở tổ chức {org[:8]}… ({bo_qua} đã có cùng dấu, bỏ qua; cutoff {m['cutoff']}); KHÔNG chạy lượt nào, 0 đồng")
    return 1 if loi else 0


if __name__ == "__main__":
    sys.exit(main())
