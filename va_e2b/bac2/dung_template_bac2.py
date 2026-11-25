"""[ghi chú] Dựng template E2B bậc 2 — phần TIỀN chạy trên máy:
GIỮ CHỖ 0,10 USD (chay_s5.giu_cho) TRƯỚC khi gửi lệnh dựng ⇒ chạy khung_template.py trong hộp tạm `b-dung-e2b` (image agentic-backend có SDK) ⇒
GHI TIỀN = số giây dựng THẬT (đo trên máy, từ lúc chạy hộp tới lúc hộp thoát — kể cả khi hỏng / hết hạn) × 0,000046 USD/s (cận trên 2 vCPU + 4 GiB;
[chưa kiểm] E2B có tính tiền giờ dựng không) cùng mã giữ chỗ. Không ghi tay.

  THU=co python3 -I chay_thu/va_e2b/bac2/dung_template_bac2.py           # 0 đồng: in khung Dockerfile (hộp --network none, KHÔNG khoá)
  DA_DUYET=co BUOI_S5=<dd_mm>/s5 TRAN_USD=<trần> python3 -I chay_thu/va_e2b/bac2/dung_template_bac2.py buoc0|dung   # CÓ TIỀN — chỉ khi đã duyệt

Điều kiện dựng thật (TEMPLATE_BAC_2): ca thật bậc 1 ĐẠT + đã duyệt tiền. Khoá: chay_thu/env/e2b.khoa.env (env_file của hộp tạm), không in."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CT = Path(__file__).resolve().parents[2]          # chay_thu/
VA = CT / "va_e2b"
KQ_DUNG = VA / "ket_qua_dung"
IMG = "capitol-local/agentic-backend:release-4.10.0"
GIU_USD = 0.10                                    # 30 phút × 0,000046 = 0,083 ⇒ 0,10 (TEMPLATE_BAC_2 mục 4)
GIA_DUNG = 0.000046
HAN_GIAY = 1800
TEN_HOP = "b-dung-e2b"


def lenh_hop(che_do: str, mang: bool) -> list[str]:
    l = ["docker", "run", "--rm", "--name", TEN_HOP, "--memory", "512m",
         "-v", f"{VA}:/srv/va_e2b:ro", "-e", f"HAN_GIAY={HAN_GIAY}", "-e", "NHAT_KY=/srv/ket_qua_dung"]
    for bien in ("TEN_TEMPLATE", "THEM_OFFICE"):   # [ghi chú] tên bản cài + cờ thêm LibreOffice / python-pptx đi vào hộp dựng (không đặt ⇒ y như v1)
        if os.environ.get(bien):
            l += ["-e", f"{bien}={os.environ[bien]}"]
    if mang:
        KQ_DUNG.mkdir(mode=0o700, exist_ok=True)
        l += ["-v", f"{KQ_DUNG}:/srv/ket_qua_dung", "-e", "CHAY_THU_E2B_KHOA"]   # [ghi chú] khoá qua env tiến trình docker (env_khoa) — --env-file giữ dấu nháy
    else:
        l += ["--network", "none"]
    return l + ["--entrypoint", "python", IMG, "-I", "/srv/va_e2b/bac2/khung_template.py", che_do]


def dong_cuoi(out: str) -> dict:
    for x in reversed(out.strip().splitlines()):
        try:
            return json.loads(x)
        except ValueError:
            continue
    return {}


def env_khoa(khoa_tep) -> dict:
    """[ghi chú] e2b.khoa.env ghi giá trị TRONG DẤU NHÁY: `docker compose` bỏ nháy, `docker run
    --env-file` KHÔNG bỏ ⇒ E2B nhận khoá có dấu nháy. Đọc file trên máy, bỏ nháy, đưa vào env của CHÍNH tiến trình docker; lệnh chỉ ghi `-e TÊN`
    (giá trị không nằm trên dòng lệnh / ps). Không in."""
    gt = None
    for d in khoa_tep.read_text(encoding="utf-8").splitlines():
        if d.startswith("CHAY_THU_E2B_KHOA="):
            gt = d.split("=", 1)[1].strip()
            if len(gt) >= 2 and gt[0] == gt[-1] and gt[0] in "\"'":
                gt = gt[1:-1]
    if not gt:
        raise SystemExit("DỪNG: e2b.khoa.env không có dòng CHAY_THU_E2B_KHOA")
    return {**os.environ, "CHAY_THU_E2B_KHOA": gt}


def main() -> int:
    if os.environ.get("THU") == "co":
        r = subprocess.run(lenh_hop("thu", mang=False), capture_output=True, text=True, timeout=120)
        print(r.stdout[-4000:])
        return r.returncode
    che_do = sys.argv[1] if len(sys.argv) > 1 else ""
    if che_do not in ("buoc0", "dung"):
        print("dùng: buoc0 | dung (hoặc THU=co)"); return 2
    if os.environ.get("DA_DUYET") != "co":
        print("DỪNG: dựng template CÓ TIỀN — cần DA_DUYET=co (xác nhận vận hành)"); return 1
    khoa = CT / "env" / "e2b.khoa.env"
    if not (khoa.is_file() and oct(khoa.stat().st_mode)[-3:] == "600" and khoa.stat().st_size > 0):
        print("DỪNG: chay_thu/env/e2b.khoa.env thiếu / rỗng / không phải 600"); return 1
    if subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^{TEN_HOP}$"], capture_output=True, text=True).stdout.strip():
        print(f"DỪNG: hộp {TEN_HOP} đang có — một lần dựng một lúc"); return 1
    sys.path.insert(0, str(CT))
    import sandbox.chay_s5 as S   # sổ tiền chung (BUOI_S5 + TRAN_USD của người chạy; trần = min với trần ngày)
    ma, tong = S.giu_cho(GIU_USD, f"e2b_dung_template_{che_do}")
    if not ma:
        print(f"DỪNG: không giữ được {GIU_USD} USD (lý do {S.ly_do_tu_choi()}, sổ + giữ = {tong})"); return 1
    print(f"giữ chỗ {GIU_USD} USD (mã {ma[:8]}), sổ trước {tong}")
    bat_dau = time.monotonic()
    kq, ma_thoat = {}, None
    try:
        r = subprocess.run(lenh_hop(che_do, mang=True), capture_output=True, text=True, timeout=HAN_GIAY + 180, env=env_khoa(khoa))
        ma_thoat, kq = r.returncode, dong_cuoi(r.stdout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", TEN_HOP], capture_output=True)
        kq = {"trang_thai": "het_han_may", "loi": "hộp dựng quá hạn trên máy"}
    finally:
        giay = round(time.monotonic() - bat_dau, 1)   # giây THẬT trên máy (≥ giây trong hộp) — cận trên
        usd = round(giay * GIA_DUNG, 6)
        S.ghi_tien(f"e2b_dung_template_{che_do}", usd, {"giay_may": giay, "giay_hop": kq.get("giay"), "trang_thai": kq.get("trang_thai"),
                                                       "template_id": kq.get("template_id"), "build_id": kq.get("build_id"), "gia_giay": GIA_DUNG,
                                                       "ma_thoat": ma_thoat}, ma_giu=ma)
        print(f"ghi sổ: {usd} USD ({giay} s × {GIA_DUNG}), trạng thái {kq.get('trang_thai')}, build {kq.get('build_id')}")
    return 0 if kq.get("trang_thai") == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
