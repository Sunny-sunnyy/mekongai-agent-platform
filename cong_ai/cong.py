"""capitol-cong-ai + Amazon Bedrock (F 18/11/2026 — bản thử độc lập trong chay_thu/va_cong_bedrock/).

Nâng cấp từ chay_thu/cong_ai/cong.py để hỗ trợ Amazon Bedrock (tài khoản 046369978969, ap-southeast-1):
  1. Nhà cung cấp kiểu `bedrock_messages`: gọi thẳng Bedrock InvokeModel với chữ ký AWS SigV4 (botocore + httpx),
     hỗ trợ đầy đủ claude-sonnet-5-5, claude-opus-5-5 và 5 mức output_config.effort (low/medium/high/xhigh/max).
     Không bật claude-haiku-5-5 và bỏ cầu dịch tạm gpt-6-luna theo quyết định 15:02 18/11 của User.
  2. Tự nhận khoá STS mới khi chạy script làm mới STS token Bedrock của công ty: đọc lại file JSON mỗi lượt từ
     /srv/bedrock_creds/bedrock-046-creds.json (gắn thư mục chứa hardlink, không đụng MekongAI/*); kiểm hạn sớm
     và tự đọc lại 1 lần nếu AWS báo ExpiredTokenException.
  3. Miễn trần ngày cho Bedrock (`mien_tran: true` trên nhà cung cấp `bedrock_messages`): vẫn ghi đầy đủ sổ cổng
     (token vào, cache_creation, cache_read, ra, thinking, usd tham khảo) nhưng KHÔNG cộng vào da_tieu() và
     KHÔNG chặn / trừ vào trần ngày — trần ngày chỉ áp cho E2B / OpenRouter / OpenAI.
  4. Sửa /v1/messages/count_tokens: tính nội bộ theo chars // 4 (khớp token_estimator.py:67, ảnh tính cố định,
     không gửi AWS /count-tokens vốn trả 400 ở ap-southeast-1 và không phình 3x byte UTF-8).
  5. Hỗ trợ stream thinking + signature delta trong su_kien_anthropic và xử lý bẫy thinking.type="disabled" (400 trên 5.5).
"""
from datetime import datetime, timezone
import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import httpx
import yaml

BANG = Path(os.environ.get("CONG_AI_BANG", "/srv/dinh_tuyen.yaml"))
GOC_DE = {k: v.rstrip("/") for k, v in (x.split("=", 1) for x in os.environ.get("CONG_AI_GOC_DE", "").split(",") if "=" in x)}
SO = Path(os.environ.get("CONG_AI_SO", "/srv/so_cong"))
TRAN_DIR = Path(os.environ.get("CONG_AI_TRAN_DIR", "/srv/tran_ngay"))
TRAN_MAC_DINH = 0.10
SO_DOC = Path(os.environ.get("CONG_AI_SO_DOC", "/srv/so_cong_doc"))
KHOA_SO = threading.Lock()
TRAN_RA = int(os.environ.get("CONG_AI_TRAN_RA", "16000"))
RA_UOC_CHOT = 32_000   # [ghi chú] token ra dùng để ƯỚC lời gọi Bedrock đang dở / lỗi (chốt bất thường, dòng cận trên) — xem _goi_bedrock_messages
CHO_BEDROCK_GIAY = float(os.environ.get("CONG_AI_CHO_BEDROCK_GIAY", "3600"))
# [ghi chú] GIỮ KẾT NỐI khi Claude viết lâu. Cổng gọi Bedrock KHÔNG phát dần (chờ trọn câu rồi
# mới phát lại) ⇒ trong lúc chờ, phía khối không nhận byte nào; thư viện phía khối cắt khi "khoảng lặng" quá ô timeout của nút (600 giây) ⇒ câu viết > ~10 phút hỏng.
# Cách nhỏ nhất: lời gọi kiểu phát dần (stream) mà quá CHO_TRUOC_PING giây chưa có trả lời từ Bedrock thì cổng MỞ luồng trả về ngay và cứ NHIP_PING giây gửi một sự
# kiện `ping` (thư viện Anthropic bỏ qua sự kiện này, nhưng đồng hồ khoảng lặng được đặt lại); có trả lời thì phát như cũ. Lời gọi xong TRƯỚC CHO_TRUOC_PING giây chạy
# Y NHƯ CŨ (mã HTTP 400 / 401 / 429 giữ nguyên — Bedrock trả các lỗi này trong vài giây). Lỗi đến SAU khi đã mở luồng không còn trả được mã HTTP ⇒ gửi sự kiện `error`
# trong luồng (sổ đã ghi ở nhánh lỗi như thường). Lời gọi không phát dần: không đổi.
CHO_TRUOC_PING = float(os.environ.get("CONG_AI_CHO_TRUOC_PING_GIAY", "20"))
NHIP_PING = float(os.environ.get("CONG_AI_NHIP_PING_GIAY", "15"))   # [ghi chú] chờ Bedrock trả lời (trước 600 s)
KHOA_TRAN = threading.Lock()
DANG_GOI: dict[str, float] = {}            # chỉ cho đường tính trần (OpenAI / OpenRouter)
DANG_GOI_MIEN_TRAN: dict[str, float] = {}   # mã ⇒ cận trên USD của lời gọi Bedrock ĐANG chạy dở ([ghi chú] cộng vào phép so với chốt an toàn)
MUC_SUY_LUAN = ("low", "medium", "high", "xhigh", "max")
THAM_SO_CAM_KHI_THINKING = ("top_p", "top_k", "parallel_tool_calls", "frequency_penalty", "presence_penalty", "seed")
app = FastAPI(title="capitol-cong-ai-bedrock")


# ---------------------------------------------------------------- sổ + trần
def _ngay() -> str:
    return time.strftime("%Y-%m-%d")


def tran_ngay() -> float:
    f = TRAN_DIR / f"{_ngay()}.txt"
    if not f.exists():
        return TRAN_MAC_DINH
    return float(f.read_text().split()[0])


# [ghi chú] CHỐT BẤT THƯỜNG cho nhà cung cấp miễn trần (Bedrock): KHÔNG phải ngân sách — rào để một vòng lặp lỗi không chạy mãi.
# Số đọc MỖI lần gọi từ <thư mục trần>/chot_bedrock.txt (A ghi; User đổi lúc nào cũng được); thiếu file ⇒ 30 USD / ngày; file có mà hỏng ⇒ NÉM ⇒ chặn.
# Tính trên MỌI dòng mien_tran của sổ cổng hôm nay (kể cả dòng cận trên của lời gọi lỗi) + cận trên của lời gọi sắp gửi.
# [ghi chú] MẶC ĐỊNH KHÔNG CHỐT: không có file chot_bedrock.txt ⇒ cổng
# KHÔNG chặn lời gọi Bedrock nào theo tiền (trước 19/11: 30 USD / ngày). Vẫn ghi sổ từng lời gọi + cộng ngày ở /suc-khoe để xem. Rào chống vòng lặp còn lại = rào
# số lần gọi của từng khối. Công tắc còn giữ: ai ghi một con số vào file đó thì chốt có hiệu lực trở lại với đúng luật cũ (file hỏng ⇒ chặn).
CHOT_MIEN_TRAN_MAC_DINH = None


def chot_mien_tran() -> float | None:
    f = TRAN_DIR / "chot_bedrock.txt"
    if not f.exists():
        return CHOT_MIEN_TRAN_MAC_DINH
    v = float(f.read_text(encoding="utf-8").split("#", 1)[0].split()[0].replace(",", "."))
    if not (v >= 0 and v == v and v != float("inf")):
        raise ValueError("chot_bedrock.txt không phải số ≥ 0")
    return v


def da_tieu_mien_tran() -> float:
    """Tổng USD các dòng mien_tran=True hôm nay (tiền Bedrock theo giá bảng, gồm dòng cận trên). File sổ hỏng ⇒ NÉM (người gọi chặn)."""
    f = SO / f"cong_{_ngay()}.jsonl"
    if not f.is_file():
        return 0.0
    return round(sum(float(x["usd"]) for x in (json.loads(d) for d in f.read_text(encoding="utf-8").splitlines() if d.strip()) if x.get("mien_tran")), 6)


def da_tieu() -> float:
    """Tổng USD ĐÃ TÍNH VÀO TRẦN NGÀY (bỏ qua dòng mien_tran=True của Amazon Bedrock)."""
    f = SO / f"cong_{_ngay()}.jsonl"
    if not f.is_file():
        return 0.0
    tong = 0.0
    for dong in f.read_text(encoding="utf-8").splitlines():
        if not dong.strip():
            continue
        x = json.loads(dong)
        if not x.get("mien_tran"):
            tong += float(x["usd"])
    return round(tong, 6)


def chay_s5_hom_nay() -> float:
    if not (SO_DOC / "DA_GAN_CG2").is_file():
        raise FileNotFoundError(f"thiếu {SO_DOC}/DA_GAN_CG2 (chay_s5 chưa gắn CG-2 ⇒ không biết tiền lượt)")
    ngay = {time.strftime("%Y-%m-%d")}
    tong = 0.0
    for f in sorted(SO_DOC.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d["ngay_cap_nhat"] in ngay:
            tong += float(d["tong_usd"]) + float(d["dang_giu_usd"])
    return round(tong, 6)


# ---------------------------------------------------------------- bảng định tuyến + khoá STS Bedrock
def doc_bang() -> dict:
    """Đọc + kiểm bảng; sai ⇒ NÉM. Chỉ cho phép mien_tran trên nhà cung cấp bedrock_messages."""
    b = yaml.safe_load(BANG.read_text(encoding="utf-8"))
    ncc, mh, dt = b["nha_cung_cap"], b["mo_hinh"], b["dinh_tuyen"]
    for ten, n in ncc.items():
        assert n["kieu"] in ("responses", "chat", "bedrock_messages") and n["goc"].startswith("https://") and n["khoa"], (
            f"nhà cung cấp {ten} sai"
        )
        if n["kieu"] == "bedrock_messages":
            assert n.get("vung"), f"nhà cung cấp bedrock {ten} thiếu vùng (vung)"
        else:
            assert not n.get("mien_tran"), f"chỉ bedrock_messages mới được đặt mien_tran ({ten})"
    for ten, m in mh.items():
        assert m["nha_cung_cap"] in ncc, f"{ten}: nhà cung cấp lạ"
        assert float(m["gia_vao"]) > 0 and float(m["gia_ra"]) > 0 and int(m["tran_ra"]) > 0, f"{ten}: giá / trần ra phải > 0"
        assert m.get("nguon"), f"{ten}: thiếu nguồn giá"
        if m.get("effort") is not None:
            assert m["effort"] in MUC_SUY_LUAN, f"{ten}: mức suy luận '{m['effort']}' không hợp lệ"
    for d in dt:
        assert d["duong"] in ("messages", "responses", "chat", "*") and d["ten"] and d["toi"] in mh, f"dòng định tuyến sai: {d}"
    return b


def tim(bang: dict, duong: str, ten: str) -> dict | None:
    for d in sorted(bang["dinh_tuyen"], key=lambda x: x["duong"] == "*"):
        if d["ten"] == ten and d["duong"] in (duong, "*"):
            ncc_ten = bang["mo_hinh"][d["toi"]]["nha_cung_cap"]
            m = dict(bang["mo_hinh"][d["toi"]], ten_muc=d["toi"])
            m["ncc"] = dict(bang["nha_cung_cap"][ncc_ten])
            m["ncc"]["goc"] = GOC_DE.get(ncc_ten, m["ncc"]["goc"])
            # [ghi chú] tuyến Bedrock KHÔNG bị kẹp bởi biến chung
            # CONG_AI_TRAN_RA: tran_ra của nó trong bảng = mức đầu ra TỐI ĐA CHÍNH MÔ HÌNH cho phép (API bắt buộc có max_tokens). Tuyến khác (luna, OpenRouter) kẹp như cũ.
            if m["ncc"]["kieu"] != "bedrock_messages":
                m["tran_ra"] = min(int(m["tran_ra"]), TRAN_RA)
            return m
    return None


def muc_suy_luan_file() -> dict[str, str]:
    """[ghi chú] {tên Capitol gửi: mức} từ <thư mục trần>/muc_suy_luan.txt, đọc MỖI lời gọi.
    Mỗi dòng "<tên tuyến> = <low | medium | high | xhigh | max>"; dòng trống / bắt đầu # bỏ qua. KHÔNG có file ⇒ {} (mọi tuyến theo mức ghi trong bảng).
    Dòng sai khuôn / mức lạ / lặp tên ⇒ NÉM (người gọi chặn lời gọi — không lặng lẽ chạy mức khác mức người đặt muốn). Đổi mức = ghi TẠI CHỖ 1 dòng,
    không sửa bảng, không tạo lại hộp; sổ cổng ghi mức THẬT đã gửi (ô ghi_chu "effort=…") + ô muc_tu_file."""
    f = TRAN_DIR / "muc_suy_luan.txt"
    try:
        dong = f.read_text(encoding="utf-8-sig").splitlines()   # [E F-K1] utf-8-sig: file lưu từ Windows có BOM đầu vẫn đọc đúng tên dòng 1
    except FileNotFoundError:
        return {}
    kq: dict[str, str] = {}
    for so, d in enumerate(dong, 1):
        d = d.strip()
        if not d or d.startswith("#"):
            continue
        ten, dau, muc = d.partition("=")
        ten, muc = ten.strip(), muc.strip()
        if not dau or not ten or muc not in MUC_SUY_LUAN or ten in kq:
            raise ValueError(f"muc_suy_luan.txt dòng {so} sai (mỗi dòng: <tên tuyến> = low|medium|high|xhigh|max, không lặp tên)")
        # [đã chốt 18/11] LUẬT: tên có đuôi mức (file mô hình của khối / script tự chọn tuyến) THẮNG — file này CHỈ áp cho TÊN TRẦN mà phía gọi không đặt được
        # mức (chat Operator, đường hồ sơ agent). Ghi tên có đuôi vào đây ⇒ NÉM (không để hai nơi cùng đặt mức cho một tuyến).
        if any(ten.endswith("-" + x) for x in MUC_SUY_LUAN):
            raise ValueError(f"muc_suy_luan.txt dòng {so}: '{ten[:60]}' đã có đuôi mức — file này chỉ nhận tên trần")
        kq[ten] = muc
    return kq


def _duong_dan_khoa_bedrock(ncc: dict) -> Path:
    k = str(ncc.get("khoa") or "")
    if k.startswith("FILE:"):
        return Path(os.environ.get("CONG_AI_BEDROCK_CREDS") or k[5:])
    if os.environ.get("CONG_AI_BEDROCK_CREDS"):
        return Path(os.environ["CONG_AI_BEDROCK_CREDS"])
    return Path(k)


def _doc_khoa_bedrock(ncc: dict, *, bo_qua_han: bool = False) -> tuple[dict, str]:
    """Đọc lại JSON STS mỗi lần gọi để tự nhận khoá ngay khi refresh-token.sh chạy."""
    p = _duong_dan_khoa_bedrock(ncc)
    if not p.is_file():
        raise PermissionError(f"thiếu file STS Bedrock '{p}'")
    d = json.loads(p.read_text(encoding="utf-8"))
    for truong in ("accessKeyId", "secretAccessKey", "sessionToken"):
        if not d.get(truong):
            raise ValueError(f"file STS Bedrock thiếu trường {truong}")
    exp = str(d.get("expiration") or "").strip()
    if exp and not bo_qua_han:
        han = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if datetime.now(timezone.utc).timestamp() >= han.timestamp() - 5:
            raise TimeoutError(
                f"STS token Bedrock đã hết hạn lúc {exp} — chạy script làm mới STS token Bedrock của công ty rồi gọi lại"
            )
    return d, exp


def _co_khoa_hop_le(m: dict) -> str | None:
    ncc = m["ncc"]
    if ncc["kieu"] == "bedrock_messages":
        if m["nha_cung_cap"] in GOC_DE:
            return None   # chế độ nhà cung cấp giả 0 đồng
        p = _duong_dan_khoa_bedrock(ncc)
        return None if p.is_file() else f"thiếu file khoá Bedrock {p} cho mục '{m['ten_muc']}'"
    return None if os.environ.get(ncc["khoa"], "") else f"thiếu khoá {ncc['khoa']} cho mục '{m['ten_muc']}'"


def usd(vao: int, ra: int, m: dict, cache_tao: int = 0, cache_doc: int = 0) -> float:
    gia_in = float(m["gia_vao"])
    gia_ct = float(m.get("gia_cache_tao") or gia_in * 1.25)
    gia_cd = float(m.get("gia_cache_doc") or gia_in * 0.1)
    return round((vao * gia_in + cache_tao * gia_ct + cache_doc * gia_cd + ra * float(m["gia_ra"])) / 1e6, 6)


def ghi(kieu: str, ten_goc: str, vao: int, ra: int, ghi_chu: str = "", m: dict | None = None,
        usd_do: float | None = None, cache_tao: int = 0, cache_doc: int = 0, suy_nghi: int = 0, them: dict | None = None) -> None:
    SO.mkdir(parents=True, exist_ok=True)
    tien = usd(vao, ra, m, cache_tao=cache_tao, cache_doc=cache_doc) if m else 0.0
    if usd_do is not None:
        tien = max(tien, round(float(usd_do), 6))
    mien = bool((m or {}).get("ncc", {}).get("mien_tran"))
    d = {
        "gio": time.strftime("%H:%M:%S"),
        "kieu": kieu,
        "mo_hinh_goc": ten_goc[:60],
        "toi": (m or {}).get("ten_muc", "—"),
        "nha_cung_cap": (m or {}).get("nha_cung_cap", "—"),
        "vao": vao,
        "ra": ra,
        "usd": tien,
        "ghi_chu": ghi_chu[:120],
    }
    if cache_tao or cache_doc or suy_nghi:
        d.update({"cache_tao": cache_tao, "cache_doc": cache_doc, "suy_nghi": suy_nghi})
    if mien:
        d["mien_tran"] = True
    if (m or {}).get("muc_tu_file"):   # [ghi chú] mức suy luận lấy từ file đè mức của dòng bảng — ô "toi" vẫn là tên dòng bảng (vd sonnet_55_med) nên ghi rõ mức THẬT
        d["muc_tu_file"] = m.get("effort")
    if them:   # [ghi chú] ô không nhạy thêm cho dòng Bedrock: ra_xin (phía gọi xin) · max_tokens (số THẬT cổng gửi) · stop_reason · dung_vi_tran_ra
        d.update(them)
    with KHOA_SO, open(SO / f"cong_{_ngay()}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    if mien and tien > 0:
        _canh_bao_moc_bedrock(tien)


# [ghi chú] Từ khi bỏ chốt tiền Bedrock (User: "không cần chốt số tiền"), tổng Bedrock trong ngày
# vượt mỗi mốc dưới đây thì cổng ghi ĐÚNG MỘT dòng cảnh báo vào sổ (kieu "canh_bao", usd 0, KHÔNG mang mien_tran ⇒ không bị cộng vào tổng) — không chặn lời gọi nào.
# Tổng ngày giữ trong bộ nhớ, lấy lại từ sổ ở lời gọi Bedrock đầu tiên của ngày / sau khi hộp khởi động lại. Đọc sổ lỗi ⇒ bỏ qua cảnh báo lần đó (không chặn, không ném).
MOC_CANH_BAO_BEDROCK = (30.0, 100.0, 300.0, 1000.0)
_TONG_BEDROCK_NGAY = {"ngay": "", "usd": 0.0}


def _canh_bao_moc_bedrock(tien: float) -> None:
    try:
        with KHOA_SO:
            ngay = _ngay()
            if _TONG_BEDROCK_NGAY["ngay"] != ngay:   # sổ đã gồm dòng vừa ghi ⇒ trừ lại để có tổng TRƯỚC dòng này
                _TONG_BEDROCK_NGAY.update(ngay=ngay, usd=max(0.0, da_tieu_mien_tran() - tien))
            truoc = _TONG_BEDROCK_NGAY["usd"]; sau = truoc + tien; _TONG_BEDROCK_NGAY["usd"] = sau
            for moc in MOC_CANH_BAO_BEDROCK:
                if truoc < moc <= sau:
                    with open(SO / f"cong_{ngay}.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({"gio": time.strftime("%H:%M:%S"), "kieu": "canh_bao", "mo_hinh_goc": "—", "toi": "—", "nha_cung_cap": "—", "vao": 0, "ra": 0, "usd": 0.0,
                                            "ghi_chu": f"CẢNH BÁO (không chặn): tổng Bedrock hôm nay vừa vượt mốc {moc:g} USD (đang {sau:.4f})"}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — cảnh báo không được làm hỏng lời gọi
        pass


def can_tren(than: dict, tran_ra: int) -> tuple[int, int]:
    return len(json.dumps(than, ensure_ascii=False).encode("utf-8")), max(int(tran_ra or 0), 1)


def _tu_choi(chu: str) -> JSONResponse:
    return JSONResponse({"type": "error", "error": {"type": "rate_limit_error", "message": "capitol-cong-ai: " + chu + " — không gọi AI"}}, 429)


def giu(than: dict, tran_ra: int, m: dict) -> tuple[str | None, JSONResponse | None]:
    if m["ncc"].get("mien_tran"):
        uoc_mt = usd(*can_tren(than, tran_ra), m)
        with KHOA_TRAN:
            try:
                chot = chot_mien_tran()
                da_mt = da_tieu_mien_tran() if chot is not None else 0.0   # không chốt ⇒ không cần đọc sổ ⇒ sổ hỏng cũng không chặn lời gọi Bedrock
            except Exception as e:
                return None, _tu_choi(f"đọc chốt an toàn Bedrock / sổ cổng lỗi ({type(e).__name__})")
            dang_mt = round(sum(DANG_GOI_MIEN_TRAN.values()), 6)   # [E G-1] nhiều lời gọi song song chưa kịp ghi sổ không được cùng lọt qua chốt
            if chot is not None and da_mt + dang_mt + uoc_mt > chot:
                ghi("messages", str(m.get("ten_muc", "—")), 0, 0, f"từ chối: chạm chốt an toàn Bedrock {chot} USD / ngày (đã {da_mt}, đang gọi {dang_mt})")
                return None, _tu_choi(f"đã chạm CHỐT AN TOÀN Bedrock hôm nay ({da_mt} + đang gọi {dang_mt} + lần này tới {uoc_mt} > {chot} USD). Đây là rào chống vòng lặp, không phải "
                                      "ngân sách — nếu là dùng thật, người vận hành nâng số trong file chot_bedrock.txt")
            ma = "mt_" + uuid.uuid4().hex
            DANG_GOI_MIEN_TRAN[ma] = uoc_mt
        return ma, None
    uoc = usd(*can_tren(than, tran_ra), m)
    with KHOA_TRAN:
        try:
            tran, s5, cong = tran_ngay(), chay_s5_hom_nay(), da_tieu()
        except Exception as e:
            return None, _tu_choi(f"đọc file trần / sổ lỗi ({type(e).__name__})")
        dang = round(sum(DANG_GOI.values()), 6)
        if cong + s5 + dang + uoc > tran:
            return None, _tu_choi(f"hết trần ngày {tran} USD (sổ cổng {cong} + sổ lượt / đang giữ {s5} + cổng đang gọi {dang} + lần này tới {uoc})")
        ma = uuid.uuid4().hex
        DANG_GOI[ma] = uoc
        return ma, None


def tra(ma: str | None) -> None:
    if not ma:
        return
    with KHOA_TRAN:
        DANG_GOI.pop(ma, None)
        DANG_GOI_MIEN_TRAN.pop(ma, None)


def ep_ra(xin, tran: int = TRAN_RA) -> int:
    try:
        v = int(xin)
    except (TypeError, ValueError):
        return tran
    return tran if v <= 0 or v > tran else v


def _khoa(m: dict) -> dict:
    k = os.environ.get(m["ncc"]["khoa"], "")
    return {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def _ngoai_bang(duong: str, ten: str, loi: str | None = None) -> JSONResponse:
    ghi(duong, ten, 0, 0, ("bảng định tuyến hỏng: " + loi) if loi else "từ chối: ngoài bảng định tuyến")
    chu = (f"bảng định tuyến hỏng ({loi}) — chặn hết" if loi else f"mô hình '{ten[:60]}' không có trong bảng định tuyến (đường {duong}) — chưa được duyệt")
    return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "capitol-cong-ai: " + chu}}, 400)


def chon(duong: str, ten: str) -> tuple[dict | None, JSONResponse | None]:
    try:
        bang = doc_bang()
    except Exception as e:
        return None, _ngoai_bang(duong, ten, f"{type(e).__name__}: {str(e)[:80]}")
    m = tim(bang, duong, ten)
    if m:
        loi_k = _co_khoa_hop_le(m)
        if loi_k:
            return None, _ngoai_bang(duong, ten, loi_k)
        if m["ncc"]["kieu"] == "bedrock_messages":   # mức suy luận đặt bằng file (chỉ tuyến Bedrock; tuyến khác bỏ qua file)
            try:
                bang_muc = muc_suy_luan_file()
                # [E F-K1] tên trong file PHẢI là tuyến Bedrock (đường messages) có trong bảng — gõ sai tên mà cổng bỏ qua thì lặng lẽ chạy mức của bảng
                for t_f in bang_muc:
                    m_f = tim(bang, "messages", t_f)
                    if not m_f or m_f["ncc"]["kieu"] != "bedrock_messages":
                        raise ValueError(f"tên '{t_f[:60]}' trong muc_suy_luan.txt không phải tuyến Bedrock nào của bảng")
                muc = bang_muc.get(ten)
            except Exception as e:
                return None, _ngoai_bang(duong, ten, f"file mức suy luận: {type(e).__name__}: {str(e)[:100]}")
            if muc:
                m["effort"], m["muc_tu_file"] = muc, True   # m là bản chép riêng của lời gọi này (tim() dựng dict mới)
        return m, None
    return None, _ngoai_bang(duong, ten)


# ---------------------------------------------------------------- ước lượng count_tokens cục bộ (0 USD)
_ANH_TUONG_DUONG_KY_TU = 1600 * 4   # theo token_estimator.py:74-89, tránh đếm byte base64 của ảnh


def _dem_ky_tu_gia_tri(v) -> int:
    if v is None:
        return 0
    if isinstance(v, str):
        return len(v)
    if isinstance(v, dict):
        if v.get("type") == "image":
            return _ANH_TUONG_DUONG_KY_TU
        return sum(_dem_ky_tu_gia_tri(x) for x in v.values()) + len(v) * 4
    if isinstance(v, list):
        return sum(_dem_ky_tu_gia_tri(x) for x in v)
    return len(str(v))


# [ghi chú] bản của F: ký tự // 4 (= 0,25 token / ký tự). ĐO THẬT 18/11 trên Sonnet 5.5 qua Bedrock: 1 lời gửi tiếng Việt có dấu +
# JSON kết quả công cụ dài 29.879 ký tự = 18.615 token ⇒ 0,623 token / ký tự (1,61 ký tự / token) ⇒ // 4 ước THIẾU ~2,5 lần ⇒ tóm gọn hội thoại chạy quá
# muộn, có thể tràn cửa sổ. Hệ số mặc định 0,75 = 0,623 × 1,2 lề (ước DƯ thì chỉ tóm gọn sớm hơn — an toàn). MỚI 1 điểm đo: đo lại khi có ≥ 30 lời gửi
# (sổ cổng có token thật). Đổi không cần sửa code: biến CONG_AI_DEM_TOKEN_MOI_KY_TU.
DEM_TOKEN_MOI_KY_TU = float(os.environ.get("CONG_AI_DEM_TOKEN_MOI_KY_TU", "0.75"))


def uoc_luong_input_tokens(a: dict) -> int:
    ky_tu = _dem_ky_tu_gia_tri(a.get("system")) + _dem_ky_tu_gia_tri(a.get("tools"))
    for msg in a.get("messages") or []:
        ky_tu += 16 + _dem_ky_tu_gia_tri(msg.get("content"))
    return max(1, int(ky_tu * DEM_TOKEN_MOI_KY_TU + 0.999))


# ---------------------------------------------------------------- Bedrock Anthropic InvokeModel
def _sang_bedrock_body(a: dict, m: dict, tran_ra: int) -> dict:
    """Chuẩn hoá thân Anthropic Messages sang AWS Bedrock InvokeModel cho Claude 5.5."""
    than = dict(a)
    for k in ("model", "stream", "context_management", "safeguards", "speed", "anthropic_speed", "betas"):
        than.pop(k, None)
    than["anthropic_version"] = "bedrock-2023-05-31"
    than["max_tokens"] = tran_ra

    # Dọn các trường nội bộ của Anthropic SDK trong messages[] mà Bedrock từ chối (tương tự bedrock-bridge.cjs)
    if isinstance(than.get("messages"), list):
        than["messages"] = [
            {k: v for k, v in msg.items() if k not in ("output_config", "safeguards")} if isinstance(msg, dict) else msg
            for msg in than["messages"]
        ]

    # Suy luận & mức effort: ưu tiên mức trong bảng (A chỉnh 1 dòng ở dinh_tuyen.yaml), nếu bảng để trống thì cho phép effort hợp lệ từ client
    eff_bang = m.get("effort")
    out_cfg_goc = than.pop("output_config", None)
    out_cfg: dict = {}
    if isinstance(out_cfg_goc, dict):
        if eff_bang in MUC_SUY_LUAN:
            out_cfg["effort"] = eff_bang
        elif out_cfg_goc.get("effort") in MUC_SUY_LUAN:
            out_cfg["effort"] = out_cfg_goc["effort"]
        if isinstance(out_cfg_goc.get("format"), dict):
            out_cfg["format"] = out_cfg_goc["format"]
    elif eff_bang in MUC_SUY_LUAN:
        out_cfg["effort"] = eff_bang
    if out_cfg:
        than["output_config"] = out_cfg

    # Bẫy đã đo thật trên 5.5: {"type": "disabled"} luôn 400 trên cả Sonnet 5.5 & Opus 5.5 ⇒ gỡ bỏ hoặc nâng sang adaptive theo mức bảng
    th = than.get("thinking")
    if isinstance(th, dict) and th.get("type") == "enabled":
        # [B 19/11 va_tom_gon_claude — ĐO THẬT 10:44 19/11: Bedrock 400 '"thinking.type.enabled" is not supported for this model. Use "thinking.type.adaptive" and
        # "output_config.effort"'] kiểu suy luận CŨ (enabled + budget_tokens) mà bộ tóm gọn hội thoại của Capitol gửi (summarizer.py:297-324) ⇒ đổi sang adaptive;
        # mức suy luận do output_config.effort của bảng quyết. Không đổi thì mọi lời gọi kiểu cũ tới Claude 5.5 đều 400.
        than["thinking"] = {"type": "adaptive"}
    elif isinstance(th, dict) and th.get("type") == "disabled":
        if "effort" in out_cfg:
            than["thinking"] = {"type": "adaptive"}
        else:
            than.pop("thinking", None)
    elif "effort" in out_cfg and not isinstance(th, dict):
        than["thinking"] = {"type": "adaptive"}

    if isinstance(than.get("thinking"), dict) and than["thinking"].get("type") in ("adaptive", "enabled"):
        than["temperature"] = 1
        for k in THAM_SO_CAM_KHI_THINKING:
            than.pop(k, None)
    # Claude 5.5 trên Bedrock luôn từ chối tool_choice ép ("tool" / "any"), kể cả khi pydantic-ai gọi cho output_type (khuôn đầu ra)
    tc = than.get("tool_choice")
    if isinstance(tc, dict) and tc.get("type") in ("tool", "any"):
        than["tool_choice"] = {"type": "auto"}
    return than


def _ky_sigv4(url: str, body_bytes: bytes, creds: dict, vung: str) -> dict:
    req = AWSRequest(
        method="POST",
        url=url,
        data=body_bytes,
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    SigV4Auth(
        Credentials(creds["accessKeyId"], creds["secretAccessKey"], creds["sessionToken"]),
        "bedrock",
        vung,
    ).add_auth(req)
    return dict(req.headers)


async def _goi_bedrock_messages(a: dict, m: dict, ten_goc: str):
    """[va_cong_ping] Bọc _goi_bedrock_tron: lời gọi phát dần chờ lâu ⇒ mở luồng + ping giữ kết nối (xem chú thích CHO_TRUOC_PING)."""
    if not a.get("stream"):
        return await _goi_bedrock_tron(a, m, ten_goc)
    viec = asyncio.ensure_future(_goi_bedrock_tron(a, m, ten_goc))
    xong, _ = await asyncio.wait({viec}, timeout=CHO_TRUOC_PING)
    if xong:
        return viec.result()   # xong sớm (kể cả lỗi sớm) ⇒ trả đúng đối tượng cũ: StreamingResponse hoặc JSONResponse có mã HTTP

    async def luong():
        so_ping = 0
        while True:
            xong, _ = await asyncio.wait({viec}, timeout=NHIP_PING)
            if xong:
                break
            so_ping += 1
            yield 'event: ping\ndata: {"type": "ping"}\n\n'
        try:
            kq = viec.result()
        except Exception as e:   # _goi_bedrock_tron tự bắt lỗi; tới đây là lỗi lạ ⇒ vẫn phải kết luồng bằng sự kiện lỗi, không treo
            yield "event: error\ndata: " + json.dumps({"type": "error", "error": {"type": "api_error", "message": f"capitol-cong-ai: lỗi {type(e).__name__} sau {so_ping} lần ping"}}, ensure_ascii=False) + "\n\n"
            return
        if isinstance(kq, StreamingResponse):
            async for mau in kq.body_iterator:
                yield mau
        else:   # lỗi MUỘN (đã mở luồng 200) ⇒ không còn mã HTTP; chuyển thân lỗi thành sự kiện error của Anthropic
            try:
                than = json.loads(bytes(kq.body).decode("utf-8"))
            except Exception:
                than = {"type": "error", "error": {"type": "api_error", "message": "capitol-cong-ai: lỗi muộn không đọc được"}}
            if not (isinstance(than, dict) and than.get("type") == "error"):
                than = {"type": "error", "error": {"type": "api_error", "message": json.dumps(than, ensure_ascii=False)[:400]}}
            yield "event: error\ndata: " + json.dumps(than, ensure_ascii=False) + "\n\n"

    return StreamingResponse(luong(), media_type="text/event-stream")


async def _goi_bedrock_tron(a: dict, m: dict, ten_goc: str):
    # [ghi chú] Tuyến Bedrock KHÔNG nghe số max_tokens nhỏ phía gọi xin
    # (khối KPI chuẩn xin 4.000, khối Agent 8.000 — đặt từ thời luna để giữ TIỀN; tiền Bedrock không tính trần). Claude 5.5 tính cả token SUY LUẬN vào đầu ra
    # ⇒ trần nhỏ có thể cắt câu trả lời giữa chừng. API bắt buộc có max_tokens ⇒ luôn gửi tran_ra của tuyến = mức TỐI ĐA CỦA MÔ HÌNH (bảng định tuyến; không kẹp).
    # Sửa ở cổng chứ không ở từng khối: (1) MỘT chỗ phủ mọi nơi gọi Claude (khối Agent, hộp phiên, chat Operator, script đo); (2) phía khối vẫn khai số nhỏ nên
    # thư viện Anthropic không đòi bật stream cho yêu cầu đầu ra lớn. Đường luna / OpenRouter KHÔNG đổi (vẫn min(số xin, trần) — tính trần tiền).
    ra_xin = a.get("max_tokens")
    body = _sang_bedrock_body(a, m, int(m["tran_ra"]))
    # ƯỚC cho chốt bất thường + dòng "cận trên" khi lỗi: KHÔNG lấy mức tối đa của mô hình (128.000 token ra ≈ 1,28 USD Sonnet / 2,56 USD Opus MỖI lời gọi dở —
    # vài lời gọi song song hoặc vài lần lỗi là tự chạm chốt oan) mà giữ mức ước cũ ≤ RA_UOC_CHOT. Chốt là rào chống vòng lặp, không phải hoá đơn: tiền thật của lời
    # gọi xong ghi theo token thật; lời gọi dở / lỗi ghi ước này.
    tran_ra = min(int(m["tran_ra"]), RA_UOC_CHOT)
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    gia_url = GOC_DE.get(m["nha_cung_cap"])
    url = f"{m['ncc']['goc']}/model/{m['id']}/invoke"

    ma, chan = giu(body, tran_ra, m)
    if chan:
        return chan
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CHO_BEDROCK_GIAY, connect=30)) as c:
            if gia_url:
                x = await c.post(url, content=body_bytes, headers={"content-type": "application/json", "x-cong-ai-test": "1"})
            else:
                try:
                    creds, _ = _doc_khoa_bedrock(m["ncc"])
                except TimeoutError as e:
                    ghi("messages", ten_goc, 0, 0, "STS hết hạn trước khi gọi — 0 đồng", m)
                    return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "capitol-cong-ai: " + str(e)}}, 401)
                except (OSError, ValueError) as e:   # [ghi chú] file khoá thiếu / hỏng / thiếu trường: CHƯA gọi lên ⇒ 0 đồng (bản F: rơi xuống nhánh chung, ghi cận trên)
                    ghi("messages", ten_goc, 0, 0, f"file khoá Bedrock không dùng được ({type(e).__name__}) — 0 đồng", m)
                    return JSONResponse({"type": "error", "error": {"type": "authentication_error",
                                         "message": f"capitol-cong-ai: file khoá Bedrock thiếu hoặc hỏng ({type(e).__name__}) — chạy script làm mới STS token Bedrock của công ty"}}, 401)
                hdrs = _ky_sigv4(url, body_bytes, creds, m["ncc"]["vung"])
                x = await c.post(url, content=body_bytes, headers=hdrs)
                if x.status_code in (400, 401, 403) and ("ExpiredToken" in x.text or "InvalidSignature" in x.text or "security token" in x.text.lower()):
                    # Trường hợp refresh-token.sh vừa xoay khoá ngay lúc request đang đi ⇒ đọc lại file 1 lần
                    creds2, _ = _doc_khoa_bedrock(m["ncc"], bo_qua_han=True)
                    if creds2.get("sessionToken") != creds.get("sessionToken"):
                        hdrs2 = _ky_sigv4(url, body_bytes, creds2, m["ncc"]["vung"])
                        x = await c.post(url, content=body_bytes, headers=hdrs2)
                    else:
                        ghi("messages", ten_goc, 0, 0, "STS bị AWS từ chối (ExpiredToken)", m)
                        return JSONResponse(
                            {"type": "error", "error": {"type": "authentication_error",
                             "message": "capitol-cong-ai: STS token hết hạn — chạy script làm mới STS token Bedrock của công ty rồi gọi lại"}},
                            401,
                        )

        if x.status_code == 429:
            # [ghi chú] Bedrock từ chối vì QUÁ HẠN MỨC GỌI của tài khoản (ThrottlingException): AWS không xử lý ⇒ không tính tiền ⇒ ghi 0 (ghi cận trên như lỗi
            # khác thì một đợt 429 + thử lại sẽ đẩy sổ chạm chốt an toàn 30 USD oan). Cổng KHÔNG tự thử lại; trả 429 rate_limit_error cho bên gọi quyết.
            ghi("messages", ten_goc, 0, 0, "Bedrock 429 quá hạn mức gọi (không tính tiền)", m)
            return _loi_len(429, f"{m['nha_cung_cap']} báo quá hạn mức gọi của tài khoản (429) — chờ vài giây rồi gọi lại: {x.text[:200]}")
        if x.status_code in (400, 403, 404, 413, 422):
            # [ghi chú] Bedrock TỪ CHỐI yêu cầu (thân sai, không có quyền, không có mô hình, quá cỡ): AWS không xử lý ⇒ không tính tiền ⇒ ghi 0 như 429.
            # Ghi cận trên thì một lỗi 400 có hệ thống × nhiều câu × thử lại làm sổ tham khảo phồng ảo và chốt bất thường khoá Bedrock cả ngày.
            ghi("messages", ten_goc, 0, 0, f"Bedrock từ chối {x.status_code} (không xử lý, không tính tiền)", m)
            return _loi_len(x.status_code, f"{m['nha_cung_cap']} trả {x.status_code}: {x.text[:300]}")
        if x.status_code != 200:   # 5xx / mã khác: có thể đã sinh một phần ⇒ giữ cận trên
            ghi("messages", ten_goc, *can_tren(body, tran_ra), f"lỗi Bedrock {x.status_code} — ghi cận trên", m)
            return _loi_len(x.status_code, f"{m['nha_cung_cap']} trả {x.status_code}: {x.text[:300]}")

        kq = x.json()
        if not isinstance(kq, dict) or not kq.get("usage"):
            raise ValueError("thiếu usage Bedrock")
        kq["model"] = ten_goc
        u = kq["usage"]
        vao = int(u.get("input_tokens") or 0)
        cache_tao = int(u.get("cache_creation_input_tokens") or 0)
        cache_doc = int(u.get("cache_read_input_tokens") or 0)
        ra = int(u.get("output_tokens") or 0)
        suy_nghi = int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
        eff = (body.get("output_config") or {}).get("effort", "")
        # [ghi chú] Claude dừng vì CHẠM TRẦN ĐẦU RA (stop_reason = max_tokens) ⇒ để dấu trên sổ: ô dung_vi_tran_ra + chữ ở ghi_chu
        dung_tran = kq.get("stop_reason") == "max_tokens"
        ghi("messages", ten_goc, vao, ra, " · ".join(c for c in (f"effort={eff}" if eff else "", "DỪNG VÌ CHẠM TRẦN ĐẦU RA" if dung_tran else "") if c), m,
            cache_tao=cache_tao, cache_doc=cache_doc, suy_nghi=suy_nghi,
            them={"ra_xin": ra_xin if isinstance(ra_xin, int) else None, "stream": bool(a.get("stream")), "max_tokens": body.get("max_tokens"), "stop_reason": str(kq.get("stop_reason") or "")[:40],   # [ghi chú] số THẬT gửi lên + lý do dừng
                  **({"dung_vi_tran_ra": True} if dung_tran else {})})
    except httpx.HTTPError as e:
        ghi("messages", ten_goc, *can_tren(body, tran_ra), f"lỗi gọi Bedrock {type(e).__name__} — ghi cận trên", m)
        return _loi_len(502, f"gọi {m['nha_cung_cap']} lỗi {type(e).__name__}")
    except Exception as e:
        ghi("messages", ten_goc, *can_tren(body, tran_ra), f"Bedrock trả về không đọc được {type(e).__name__} — ghi cận trên", m)
        return _loi_len(502, f"{m['nha_cung_cap']} trả về không đọc được ({type(e).__name__})")
    finally:
        tra(ma)

    if a.get("stream"):
        return StreamingResponse(su_kien_anthropic(kq), media_type="text/event-stream")
    return JSONResponse(kq)


# ---------------------------------------------------------------- Anthropic ⇄ Responses
def _chu(noi_dung) -> str:
    if isinstance(noi_dung, str):
        return noi_dung
    return "".join(b.get("text", "") for b in (noi_dung or []) if isinstance(b, dict) and b.get("type") == "text")


def sang_responses(a: dict) -> dict:
    vao = []
    for m in a.get("messages", []):
        vai, nd = m.get("role"), m.get("content")
        if isinstance(nd, str):
            nd = [{"type": "text", "text": nd}]
        chu_loai = "input_text" if vai == "user" else "output_text"
        dem = []
        def xa():
            if dem:
                vao.append({"role": vai, "content": list(dem)}); dem.clear()
        for b in nd or []:
            t = b.get("type")
            if t == "text":
                dem.append({"type": chu_loai, "text": b.get("text", "")})
            elif t == "image" and vai == "user":
                s = b.get("source") or {}
                url = s.get("url") if s.get("type") == "url" else f"data:{s.get('media_type')};base64,{s.get('data')}"
                dem.append({"type": "input_image", "image_url": url})
            elif t == "tool_use":
                xa(); vao.append({"type": "function_call", "call_id": b["id"], "name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)})
            elif t == "tool_result":
                xa(); out = _chu(b.get("content")) if not isinstance(b.get("content"), str) else b["content"]
                vao.append({"type": "function_call_output", "call_id": b["tool_use_id"], "output": ("[LỖI] " if b.get("is_error") else "") + out})
        xa()
    r = {"model": "", "input": vao, "store": False, "reasoning": {"effort": "low"},
         "max_output_tokens": int(a.get("max_tokens") or 4096)}
    he = a.get("system")
    if he:
        r["instructions"] = _chu(he) if not isinstance(he, str) else he
    if a.get("tools"):
        r["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema") or {"type": "object"}}
                      for t in a["tools"] if t.get("name") and t.get("type") in (None, "custom")]
        tc = a.get("tool_choice") or {}
        r["tool_choice"] = {"auto": "auto", "any": "required", "none": "none"}.get(tc.get("type"), "auto") if tc.get("type") != "tool" \
            else {"type": "function", "name": tc.get("name")}
        if tc.get("disable_parallel_tool_use"):
            r["parallel_tool_calls"] = False
    return r


def sang_anthropic(rs: dict, ten_goc: str) -> dict:
    khoi = []
    for it in rs.get("output", []):
        if it.get("type") == "message":
            for c in it.get("content", []):
                if c.get("type") == "output_text" and c.get("text"):
                    khoi.append({"type": "text", "text": c["text"]})
        elif it.get("type") == "function_call":
            try:
                inp = json.loads(it.get("arguments") or "{}")
            except ValueError:
                inp = {"_arguments_loi": it.get("arguments")}
            khoi.append({"type": "tool_use", "id": it.get("call_id") or ("toolu_" + uuid.uuid4().hex[:20]), "name": it.get("name"), "input": inp})
    if not khoi:
        khoi.append({"type": "text", "text": ""})
    u = rs.get("usage") or {}
    ly_do = "tool_use" if any(k["type"] == "tool_use" for k in khoi) else \
        ("max_tokens" if (rs.get("incomplete_details") or {}).get("reason") == "max_output_tokens" else "end_turn")
    return {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant", "model": ten_goc, "content": khoi,
            "stop_reason": ly_do, "stop_sequence": None,
            "usage": {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0),
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


def su_kien_anthropic(m: dict):
    """Dựng chuỗi SSE Anthropic đầy đủ, giữ cả khối thinking + signature (quan trọng với Claude 5.5)."""
    def e(ten, d):
        return f"event: {ten}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n"
    u = m.get("usage") or {}
    dau = dict(m, content=[], stop_reason=None, usage={
        "input_tokens": int(u.get("input_tokens") or 0),
        "output_tokens": 0,
        "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
    })
    yield e("message_start", {"type": "message_start", "message": dau})
    for i, k in enumerate(m["content"]):
        t = k.get("type")
        if t == "text":
            yield e("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            yield e("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": k.get("text", "")}})
            for cit in k.get("citations") or []:
                yield e("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "citations_delta", "citation": cit}})
        elif t == "thinking":
            yield e("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": ""}})
            if k.get("thinking"):
                yield e("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": k["thinking"]}})
            if k.get("signature"):
                yield e("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": k["signature"]}})
        elif t == "redacted_thinking":
            yield e("content_block_start", {"type": "content_block_start", "index": i, "content_block": k})
        else:
            yield e("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": k["id"], "name": k["name"], "input": {}}})
            yield e("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": json.dumps(k.get("input") or {}, ensure_ascii=False)}})
        yield e("content_block_stop", {"type": "content_block_stop", "index": i})
    yield e("message_delta", {"type": "message_delta", "delta": {"stop_reason": m.get("stop_reason"), "stop_sequence": m.get("stop_sequence")}, "usage": {"output_tokens": int(u.get("output_tokens") or 0)}})
    yield e("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------- Anthropic ⇄ Chat Completions (OpenRouter)
def sang_chat(a: dict) -> dict:
    tin = []
    he = a.get("system")
    if he:
        tin.append({"role": "system", "content": _chu(he) if not isinstance(he, str) else he})
    for m in a.get("messages", []):
        vai, nd = m.get("role"), m.get("content")
        if isinstance(nd, str):
            tin.append({"role": vai, "content": nd}); continue
        chu, goi, phan = [], [], []
        for b in nd or []:
            t = b.get("type")
            if t == "text":
                chu.append(b.get("text", ""))
            elif t == "image" and vai == "user":
                sr = b.get("source") or {}
                phan.append({"type": "image_url", "image_url": {"url": sr.get("url") if sr.get("type") == "url" else f"data:{sr.get('media_type')};base64,{sr.get('data')}"}})
            elif t == "tool_use":
                goi.append({"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}})
            elif t == "tool_result":
                out = b["content"] if isinstance(b.get("content"), str) else _chu(b.get("content"))
                tin.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": ("[LỖI] " if b.get("is_error") else "") + out})
        if vai == "assistant" and (chu or goi):
            d = {"role": "assistant", "content": "\n".join(chu) or None}
            if goi:
                d["tool_calls"] = goi
            tin.append(d)
        elif vai == "user" and (chu or phan):
            tin.append({"role": "user", "content": ([{"type": "text", "text": "\n".join(chu)}] if chu else []) + phan if phan else "\n".join(chu)})
    r = {"model": "", "messages": tin, "max_tokens": int(a.get("max_tokens") or TRAN_RA), "usage": {"include": True}}
    if a.get("tools"):
        r["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema") or {"type": "object"}}}
                      for t in a["tools"] if t.get("name") and t.get("type") in (None, "custom")]
        tc = a.get("tool_choice") or {}
        r["tool_choice"] = {"type": "function", "function": {"name": tc.get("name")}} if tc.get("type") == "tool" else \
            {"auto": "auto", "any": "required", "none": "none"}.get(tc.get("type"), "auto")
    return r


def chat_sang_anthropic(rs: dict, ten_goc: str) -> dict:
    ch = (rs.get("choices") or [{}])[0]; msg = ch.get("message") or {}
    khoi = [{"type": "text", "text": msg["content"]}] if msg.get("content") else []
    for g in msg.get("tool_calls") or []:
        try:
            inp = json.loads((g.get("function") or {}).get("arguments") or "{}")
        except ValueError:
            inp = {"_arguments_loi": (g.get("function") or {}).get("arguments")}
        khoi.append({"type": "tool_use", "id": g.get("id") or ("toolu_" + uuid.uuid4().hex[:20]), "name": (g.get("function") or {}).get("name"), "input": inp})
    if not khoi:
        khoi.append({"type": "text", "text": ""})
    u = rs.get("usage") or {}
    ly_do = "tool_use" if any(k["type"] == "tool_use" for k in khoi) else ("max_tokens" if ch.get("finish_reason") == "length" else "end_turn")
    return {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant", "model": ten_goc, "content": khoi, "stop_reason": ly_do,
            "stop_sequence": None, "usage": {"input_tokens": int(u.get("prompt_tokens") or 0), "output_tokens": int(u.get("completion_tokens") or 0),
                                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}, "_cost": u.get("cost")}


# ---------------------------------------------------------------- đường gọi
def _loi_len(ma_http: int, chu: str) -> JSONResponse:
    # [ghi chú] loại lỗi theo mã HTTP đúng khuôn Anthropic ⇒ SDK / pydantic-ai phân biệt "quá hạn mức gọi" (429, thử lại được) với lỗi khác
    loai = {429: "rate_limit_error", 503: "overloaded_error", 529: "overloaded_error"}.get(ma_http, "api_error")
    return JSONResponse({"type": "error", "error": {"type": loai, "message": "capitol-cong-ai: " + chu}}, ma_http)


@app.post("/v1/messages")
async def messages(req: Request):
    a = await req.json()
    ten_goc = str(a.get("model") or "")
    m, chan = chon("messages", ten_goc)
    if chan:
        return chan
    if m["ncc"]["kieu"] == "bedrock_messages":
        return await _goi_bedrock_messages(a, m, ten_goc)
    if m["ncc"]["kieu"] == "responses":
        r, duong_len = sang_responses(a), "/v1/responses"
        r["max_output_tokens"] = tran_ra = ep_ra(r.get("max_output_tokens"), m["tran_ra"])
    else:
        r, duong_len = sang_chat(a), "/v1/chat/completions"
        r["max_tokens"] = tran_ra = ep_ra(r.get("max_tokens"), m["tran_ra"])
    r["model"] = m["id"]
    ma, chan = giu(r, tran_ra, m)
    if chan:
        return chan
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600)) as c:
            x = await c.post(m["ncc"]["goc"] + duong_len, json=r, headers=_khoa(m))
        if x.status_code != 200:
            ghi("messages", ten_goc, *can_tren(r, tran_ra), f"lỗi lên {x.status_code} — ghi cận trên", m)
            return _loi_len(x.status_code, f"{m['nha_cung_cap']} trả {x.status_code}: {x.text[:300]}")
        tho = x.json()
        if not isinstance(tho, dict) or not tho.get("usage"):
            raise ValueError("thiếu usage")
        kq = sang_anthropic(tho, ten_goc) if m["ncc"]["kieu"] == "responses" else chat_sang_anthropic(tho, ten_goc)
        cost = kq.pop("_cost", None)
        ghi("messages", ten_goc, kq["usage"]["input_tokens"], kq["usage"]["output_tokens"], "", m, cost)
    except httpx.HTTPError as e:
        ghi("messages", ten_goc, *can_tren(r, tran_ra), f"lỗi gọi lên {type(e).__name__} — ghi cận trên", m)
        return _loi_len(502, f"gọi {m['nha_cung_cap']} lỗi {type(e).__name__}")
    except Exception as e:
        ghi("messages", ten_goc, *can_tren(r, tran_ra), f"trả về không đọc được {type(e).__name__} — ghi cận trên", m)
        return _loi_len(502, f"{m['nha_cung_cap']} trả về không đọc được ({type(e).__name__})")
    finally:
        tra(ma)
    if a.get("stream"):
        return StreamingResponse(su_kien_anthropic(kq), media_type="text/event-stream")
    return JSONResponse(kq)


@app.post("/v1/messages/count_tokens")
async def dem_token(req: Request):
    a = await req.json()
    return JSONResponse({"input_tokens": uoc_luong_input_tokens(a)})


async def _chuyen(duong_len: str, than: dict, ten_goc: str, kieu: str, tran_ra: int, m: dict):
    ma, chan = giu(than, tran_ra, m)
    if chan:
        return chan
    goc = m["ncc"]["goc"]
    if not than.get("stream"):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600)) as c:
                x = await c.post(goc + duong_len, json=than, headers=_khoa(m))
            try:
                u = (x.json().get("usage") or {}) if x.headers.get("content-type", "").startswith("application/json") else {}
                vao = int(u.get("input_tokens", u.get("prompt_tokens", 0)) or 0); ra = int(u.get("output_tokens", u.get("completion_tokens", 0)) or 0)
            except Exception:
                u = {}
            if x.status_code == 200 and u:
                ghi(kieu, ten_goc, vao, ra, "", m, u.get("cost"))
            else:
                ghi(kieu, ten_goc, *can_tren(than, tran_ra), f"lỗi lên {x.status_code} / thiếu usage — ghi cận trên", m)
            return Response(x.content, x.status_code, media_type=x.headers.get("content-type", "application/json"))
        except httpx.HTTPError as e:
            ghi(kieu, ten_goc, *can_tren(than, tran_ra), f"lỗi gọi lên {type(e).__name__} — ghi cận trên", m)
            return JSONResponse({"error": {"message": f"capitol-cong-ai: gọi {m['nha_cung_cap']} lỗi {type(e).__name__}"}}, 502)
        finally:
            tra(ma)

    async def luong():
        vao = ra = 0; cost = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600)) as c:
                async with c.stream("POST", goc + duong_len, json=than, headers=_khoa(m)) as x:
                    async for dong in x.aiter_lines():
                        if dong.startswith("data: ") and '"usage"' in dong:
                            try:
                                d = json.loads(dong[6:])
                                u = (d.get("response") or {}).get("usage") or d.get("usage") or {}
                                vao = int(u.get("input_tokens", u.get("prompt_tokens", vao)) or vao)
                                ra = int(u.get("output_tokens", u.get("completion_tokens", ra)) or ra)
                                cost = u.get("cost", cost)
                            except ValueError:
                                pass
                        yield dong + "\n"
        finally:
            if vao or ra:
                ghi(kieu, ten_goc, vao, ra, "luồng", m, cost)
            else:
                ghi(kieu, ten_goc, *can_tren(than, tran_ra), "luồng thiếu usage — ghi cận trên", m)
            tra(ma)
    return StreamingResponse(luong(), media_type="text/event-stream")


@app.post("/v1/responses")
async def responses(req: Request):
    t = await req.json()
    ten_goc = str(t.get("model") or "")
    m, chan = chon("responses", ten_goc)
    if chan:
        return chan
    if m["ncc"]["kieu"] != "responses":
        return _ngoai_bang("responses", ten_goc, f"mục '{m['ten_muc']}' ({m['nha_cung_cap']}) không nhận đường /v1/responses")
    t["model"] = m["id"]
    t.pop("temperature", None); t.pop("top_p", None)
    t["max_output_tokens"] = ep_ra(t.get("max_output_tokens"), m["tran_ra"])
    return await _chuyen("/v1/responses", t, ten_goc, "responses", t["max_output_tokens"], m)


@app.post("/v1/chat/completions")
async def chat(req: Request):
    t = await req.json()
    ten_goc = str(t.get("model") or "")
    m, chan = chon("chat", ten_goc)
    if chan:
        return chan
    if m["ncc"]["kieu"] == "bedrock_messages":
        return _ngoai_bang("chat", ten_goc, f"mục '{m['ten_muc']}' ({m['nha_cung_cap']}) chỉ nhận đường /v1/messages")
    t["model"] = m["id"]
    t.pop("temperature", None); t.pop("top_p", None)
    tran = ep_ra(t.get("max_completion_tokens") or t.get("max_tokens"), m["tran_ra"])
    if m["nha_cung_cap"] == "openai":
        t["max_completion_tokens"] = tran; t.pop("max_tokens", None)
    else:
        t["max_tokens"] = tran; t.pop("max_completion_tokens", None); t["usage"] = {"include": True}
    if t.get("stream"):
        t["stream_options"] = dict(t.get("stream_options") or {}, include_usage=True)
    return await _chuyen("/v1/chat/completions", t, ten_goc, "chat", tran, m)


@app.get("/v1/models")
async def models():
    try:
        b = doc_bang()
    except Exception as e:
        return JSONResponse({"error": {"message": f"capitol-cong-ai: bảng định tuyến hỏng ({type(e).__name__})"}}, 503)
    return {"object": "list", "data": [{"id": d["ten"], "object": "model", "owned_by": f"capitol-cong-ai⇒{d['toi']}"} for d in b["dinh_tuyen"]]}


@app.api_route("/v1/{duong:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def khac(duong: str):
    return JSONResponse({"type": "error", "error": {"type": "invalid_request_error",
                         "message": f"capitol-cong-ai: đường /v1/{duong} không mở (chỉ mô hình chữ — embeddings / ảnh / âm thanh giữ TẮT)"}}, 400)


@app.get("/suc-khoe")
async def suc_khoe():
    try:
        s5 = chay_s5_hom_nay()
    except Exception as e:
        s5 = f"lỗi {type(e).__name__}"
    try:
        b = doc_bang(); bang = {"phien_ban": b.get("phien_ban"), "so_dong": len(b["dinh_tuyen"]), "mo_hinh": sorted(b["mo_hinh"])}
    except Exception as e:
        bang = f"HỎNG {type(e).__name__}"
    # [ghi chú] khoá Bedrock là loại tạm vài tiếng ⇒ in số PHÚT còn lại (không in giá trị khoá) để giao diện + quản trị cảnh báo TRƯỚC khi hết
    sts = {}
    if not isinstance(bang, str):
        for ten_ncc, n in b["nha_cung_cap"].items():
            if n.get("kieu") != "bedrock_messages":
                continue
            try:
                _, exp = _doc_khoa_bedrock(n, bo_qua_han=True)
                con = round((datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp() - datetime.now(timezone.utc).timestamp()) / 60, 1) if exp else None
                sts[ten_ncc] = {"het_han_utc": exp or None, "con_phut": con, "con_han": con is None or con > 0}
            except Exception as e:
                sts[ten_ncc] = {"loi": type(e).__name__, "con_han": False}
    return {
        "ok": not isinstance(bang, str),
        "bedrock_sts": sts,
        "bang": bang,
        "tran_ngay": tran_ngay(),
        "da_tieu": da_tieu(),
        "chay_s5_va_dang_giu": s5,
        "dang_goi": {"so": len(DANG_GOI), "usd": round(sum(DANG_GOI.values()), 6)},
        "dang_goi_mien_tran": len(DANG_GOI_MIEN_TRAN),   # giữ là SỐ (bộ thử của F so == 0)
        "dang_goi_mien_tran_usd": round(sum(DANG_GOI_MIEN_TRAN.values()), 6),
    }
