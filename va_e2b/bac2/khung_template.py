"""[ghi chú] Khung template E2B bậc 2 `chay-thu-e2b-doc:v1` — chạy TRONG hộp tạm b-dung-e2b (image agentic-backend có SDK
e2b 2.46.4). Script trên máy dung_template_bac2.py gọi file này; KHÔNG gọi thẳng (tiền giữ chỗ / ghi sổ nằm ở script máy).

  python -I khung_template.py thu      # chỉ in khung JSON SDK sẽ gửi — KHÔNG mạng, KHÔNG khoá (chạy được với --network none)
  python -I khung_template.py buoc0    # dựng NHỎ: chỉ đo bản Python + gói nền của code-interpreter-v1 (để chọn wheel) — gọi E2B, có tiền dựng
  python -I khung_template.py dung     # dựng thật — gọi E2B, có tiền dựng

Dòng CUỐI luôn là JSON {"che_do", "trang_thai", "giay", "template_id", "build_id", "loi"} — script máy đọc dòng này. Khoá: biến CHAY_THU_E2B_KHOA (env_file),
truyền thẳng vào tham số api_key, không in, không ghi log. Quá HAN_GIAY ⇒ gọi API xoá template đang dựng ([chưa kiểm] xoá có dừng bản dựng không —
script máy vẫn ghi tiền theo số giây thật)."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

DAY = Path(__file__).resolve().parent.parent     # /srv/va_e2b (gắn ro chay_thu/va_e2b): requirements_e2b_doc.txt + mekong_doc/
TEN = os.environ.get("TEN_TEMPLATE") or "chay-thu-e2b-doc:v1"   # [ghi chú] tên bản cài lấy từ biến (mặc định y như cũ)
# [ghi chú] THEM_OFFICE=co ⇒ bản v2: thêm LibreOffice KHÔNG giao diện (để bước "tính lại Excel" của dịch vụ làm file chạy được trên máy ảo —
# lượt thật 01706f9a: ô công thức không có giá trị tính sẵn) + python-pptx (cho khối PowerPoint ở nấc 2). Không đặt biến ⇒ khung y như v1.
THEM_OFFICE = os.environ.get("THEM_OFFICE") == "co"
TEN_BUOC0 = "chay-thu-e2b-do-nen:v0"
HAN_GIAY = int(os.environ.get("HAN_GIAY", "1800"))   # 30 phút (TEMPLATE_BAC_2 mục 4b)
NHAT_KY = Path(os.environ.get("NHAT_KY", "/srv/ket_qua_dung")) / f"dung_{time.strftime('%Y%m%d_%H%M%S')}.log"


def khung(che_do: str):
    from e2b import Template
    t = Template(file_context_path=str(DAY), file_ignore_patterns=["**/__pycache__/**", "**/*.pyc"]).from_template("code-interpreter-v1")
    if che_do != "buoc0" and THEM_OFFICE:
        return them_office(_khung_v1(t))
    return _khung_v1(t, che_do)


def _khung_v1(t, che_do: str = "dung"):
    if che_do == "buoc0":
        return t.run_cmd("python -V; pip list --format=freeze; cat /etc/os-release | head -3; nproc; grep MemTotal /proc/meminfo", user="root")
    return (t.run_cmd("apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-vie libgl1 libgomp1 "
                      "libmagic1 fonts-dejavu-core && rm -rf /var/lib/apt/lists/* && mkdir -p /opt/chay_thu "
                      "&& dpkg-query -W -f='${Package}=${Version}\\n' > /opt/chay_thu/apt_da_cai.txt", user="root")
             .copy("requirements_e2b_doc.txt", "/opt/chay_thu/requirements_e2b_doc.txt", user="root")
             # --require-hashes: mọi wheel phải đúng băm uv.lock mekongai e2b4dd97; --no-deps + pip check: không kéo gói ngoài danh sách
             .run_cmd("pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps -r /opt/chay_thu/requirements_e2b_doc.txt && pip check",
                      user="root")
             .copy("mekong_doc", "/opt/mekong_doc", user="root")
             .run_cmd("cd /opt/mekong_doc && sha256sum -c --quiet MEKONG_DOC_SHA256.txt && chmod -R a+rX /opt/mekong_doc", user="root")
             # [ghi chú] nạp thử đủ gói + module bọc NGAY khi dựng — lỗi (vd numpy 2.3.5 của nền lệch opencv / onnxruntime trên Python 3.13)
             # ⇒ bước này thoát khác 0 ⇒ bản dựng HỎNG, template không được đẩy. Chỉ gói MÌNH cài + numpy nền (openpyxl / pandas của nền: thử ở mục 5)
             .run_cmd("cd /opt/mekong_doc && PDF_VLM_ENABLED=false PDF_DOCLING_ORDER=false python -c \"import fitz, pymupdf.layout, pytesseract, cv2, docx, "
                      "pdf2image, onnxruntime, yaml, lxml, PIL, numpy; import doc_tai_lieu; print('NAP_DU', numpy.__version__)\"", user="root")
             # [ghi chú] BỎ bước gỡ setuid: gỡ cả /usr/bin/sudo ⇒ lệnh khởi động của nền "sudo systemctl start jupyter" chết (dựng 3d760c00 hỏng);
             # nền cho `user` sudo KHÔNG mật khẩu ⇒ gỡ setuid không chặn leo quyền. Ranh giới: máy dùng một lần + mạng theo cờ + không khoá AI + R12.
             # pip KHÔNG gỡ: User cho cài gói lúc chạy (install_packages) khi mạng ra bật
             .set_envs({"PYTHONPATH": "/opt/mekong_doc", "PDF_VLM_ENABLED": "false", "PDF_DOCLING_ORDER": "false", "OMP_THREAD_LIMIT": "1"}))


def them_office(t):
    """Bản v2: cài thêm rồi THỬ NGAY lúc dựng (thiếu lệnh soffice / không nạp được pptx ⇒ bản dựng hỏng, không đẩy bản cài)."""
    return (t.run_cmd("apt-get update && apt-get install -y --no-install-recommends libreoffice-calc libreoffice-writer libreoffice-impress "
                      "&& rm -rf /var/lib/apt/lists/* && dpkg-query -W -f='${Package}=${Version}\\n' > /opt/chay_thu/apt_da_cai.txt", user="root")
             .run_cmd("pip install --no-cache-dir --only-binary=:all: python-pptx==1.0.2 && pip check", user="root")
             .run_cmd("soffice --headless --version && python -c \"import pptx, docx, openpyxl; print('NAP_OFFICE', pptx.__version__)\"", user="root"))


def ket(**kw):
    d = {"che_do": None, "trang_thai": None, "giay": None, "template_id": None, "build_id": None, "loi": None} | kw
    print(json.dumps(d, ensure_ascii=False))
    return 0 if d["trang_thai"] == "ready" or (d["che_do"] == "thu" and not d["loi"]) else 1


async def dung(che_do: str) -> int:
    from e2b import AsyncTemplate
    khoa = (os.environ.get("CHAY_THU_E2B_KHOA") or "").strip()
    if not khoa:
        return ket(che_do=che_do, loi="thiếu CHAY_THU_E2B_KHOA")
    NHAT_KY.parent.mkdir(parents=True, exist_ok=True)
    f = NHAT_KY.open("a", encoding="utf-8")
    bat_dau = time.monotonic()
    ten, cpu, ram = (TEN_BUOC0, 1, 1024) if che_do == "buoc0" else (TEN, 1, 1024)   # người vận hành 1 vCPU / 1 GiB
    info = await AsyncTemplate.build_in_background(khung(che_do), ten, cpu_count=cpu, memory_mb=ram, api_key=khoa)
    tid, bid = getattr(info, "template_id", None), getattr(info, "build_id", None)
    f.write(f"bắt đầu {ten} template_id={tid} build_id={bid}\n"); f.flush()
    trang, offset = None, 0
    while time.monotonic() - bat_dau < HAN_GIAY:
        st = await AsyncTemplate.get_build_status(info, logs_offset=offset, api_key=khoa)
        for x in getattr(st, "log_entries", None) or []:
            f.write(f"{getattr(x, 'timestamp', '')} {getattr(x, 'level', '')} {getattr(x, 'message', x)}\n")
            offset += 1
        f.flush()
        trang = str(getattr(st, "status", "") or "").split(".")[-1].lower()
        if trang in ("ready", "error"):
            break
        await asyncio.sleep(5)
    giay = round(time.monotonic() - bat_dau, 1)
    if trang not in ("ready", "error"):
        trang = "het_han"
        try:   # [chưa kiểm] xoá template có dừng bản đang dựng không — tiền vẫn theo giây thật ở script máy
            from e2b.api.client.api.templates import delete_templates_template_id
            from e2b.api import ApiClient
            from e2b.connection_config import ConnectionConfig
            with ApiClient(ConnectionConfig(api_key=khoa)) as c:
                delete_templates_template_id.sync_detailed(template_id=tid, client=c)
            f.write("hết hạn ⇒ đã gọi xoá template\n")
        except Exception as e:
            f.write(f"hết hạn ⇒ xoá template LỖI: {type(e).__name__}\n")
    f.close()
    return ket(che_do=che_do, trang_thai=trang, giay=giay, template_id=tid, build_id=bid, loi=None if trang == "ready" else f"trạng thái {trang}")


def main() -> int:
    che_do = sys.argv[1] if len(sys.argv) > 1 else ""
    if che_do == "thu":
        from e2b import Template
        try:
            print(Template.to_json(khung("dung")))   # to_dockerfile không làm được với template dựng từ template khác (SDK ValueError)
            return ket(che_do="thu", trang_thai="in_khung")
        except Exception as e:
            return ket(che_do="thu", loi=f"{type(e).__name__}: {e}"[:300])
    if che_do in ("buoc0", "dung"):
        try:
            return asyncio.run(dung(che_do))
        except Exception as e:
            return ket(che_do=che_do, loi=f"{type(e).__name__}"[:120])   # không in thông điệp lỗi đầy đủ: có thể chứa URL / mã
    print("dùng: thu | buoc0 | dung", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
