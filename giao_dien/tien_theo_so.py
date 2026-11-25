"""[ghi chú kỹ thuật] Màn lượt hiện "Tiền cả lượt" TOÀN SỐ 0 cho lượt có khối Chạy code / khối làm file (Excel, Word, PowerPoint, phân tích dữ liệu, biểu đồ):
hàm chi_phi của trang chỉ đọc lời gọi AI lưu trong kết quả khối (message_history), mà các khối này không lưu ở đó. Tiền của chúng VẪN có trong sổ (đo từ nhật ký hộp + sổ máy ảo).
⇒ đường /api/runs/<phiên>/cost trả thêm ô "so" = dòng sổ của đúng phiên đó, để màn lượt hiện số của sổ. Chỉ ĐỌC file sổ; không đổi cách tính, không đổi sổ.
  tim(thư mục chay_thu, mã phiên) ⇒ None (sổ không có phiên này) | {"usd_tinh_tran", "usd_bedrock_tham_khao", "token_vao", "token_ra", "so_dong", "gio"}"""
import json
import os
from pathlib import Path


def tim(ct, phien: str, toi_da_file: int = 10):
    if not isinstance(phien, str) or len(phien) < 8:
        return None
    try:
        goc_rt = Path(os.environ["CAPITOL_RUNTIME_DIR"]).expanduser() if os.environ.get("CAPITOL_RUNTIME_DIR") else Path(ct, "runtime")
        tep = sorted(goc_rt.glob("*/s5/so_tien.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:toi_da_file]
    except OSError:
        return None
    for f in tep:
        try:
            dong = [x for x in (json.loads(f.read_text(encoding="utf-8")).get("luot") or []) if isinstance(x, dict) and x.get("session") == phien]
        except (OSError, ValueError):
            continue
        if dong:
            so = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            tk = [x.get("token_bedrock") if isinstance(x.get("token_bedrock"), dict) else {} for x in dong]
            return {"usd_tinh_tran": round(sum(so(x.get("usd")) for x in dong), 6), "usd_bedrock_tham_khao": round(sum(so(x.get("usd_bedrock_tham_khao")) for x in dong), 6),
                    "token_vao": int(sum(so(t.get("vao")) for t in tk)), "token_ra": int(sum(so(t.get("ra")) for t in tk)), "so_dong": len(dong), "gio": dong[-1].get("gio")}
    return None


if __name__ == "__main__":   # python3 -I -B tien_theo_so.py <mã phiên> …  — in ô "so" của từng phiên (chỉ đọc)
    import sys
    ct = Path(__file__).resolve().parent.parent
    ct = ct if (ct / "runtime").is_dir() else ct.parent
    for p in sys.argv[1:]:
        print(p[:8], json.dumps(tim(ct, p), ensure_ascii=False))
