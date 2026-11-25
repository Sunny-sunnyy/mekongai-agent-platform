"""Kiểm thử ngoại tuyến (0 đồng) việc nới hai đuôi .html + .xlsb và siết dò khóa UTF-16 ở rào kiểm tra tệp vào E2B.
Chạy trên WSL, không gọi AI, không tạo máy ảo, không đụng hộp: trích ĐÚNG đoạn hàm rào (từ `def _e2b_ds_cho_phep` tới trước `class _E2bChayThu`)
của sandbox_runtime.py rồi chạy; trần cỡ file thử trên ĐÚNG hàm _kiem_file_vao của e2b_chat_service.py (ba hàm nó gọi sang được thay bằng bản giả).
  python3 -I -B chay_thu/va_e2b_duoi_html_xlsb_v2/thu_duoi.py        # bản MỚI trong gói — mọi ca phải ĐẠT
  python3 -I -B chay_thu/va_e2b_duoi_html_xlsb_v2/thu_duoi.py cu     # bản ĐANG GẮN — các ca [ĐỎ TRƯỚC] (D1–D3 hai đuôi mới; U1–U3 khoá UTF-16 ở đuôi cũ) phải HỎNG, các ca [ÂM] vẫn ĐẠT (ở bản cũ chỉ đòi "bị chặn")
  python3 -I -B chay_thu/va_e2b_duoi_html_xlsb_v2/thu_duoi.py <đường dẫn sandbox_runtime.py>   # thử một bản bất kỳ (vd bản trong hộp đã chép ra)
Sàn giữ: an toàn (khoá không lên máy ảo có mạng ra; loại file lạ không lên). Mẫu khoá được GHÉP lúc chạy (không ghi nguyên chuỗi giống khoá vào file này)."""
import ast, hashlib, io, os, re, struct, sys, types, zipfile
from pathlib import Path

GOI = Path(__file__).resolve().parent; CT = GOI.parent
DOI = sys.argv[1] if len(sys.argv) > 1 else ""
CU = DOI == "cu"
F = (CT / "sua_doi/workflow/node/base/agent/sandbox_runtime.py") if CU else (Path(DOI) if DOI else GOI / "sandbox_runtime.py")
GOC = CT / "sua_doi/workflow/node/base/agent/sandbox_runtime.py.truoc_duoi_html_xlsb"   # có sau khi gắn; chưa gắn thì gốc = file đang gắn
src = F.read_text(encoding="utf-8")
doan = src[src.index("def _e2b_ds_cho_phep"):src.index("class _E2bChayThu")]
ns = {"os": os, "_re": re}; exec(compile(doan, str(F), "exec"), ns)
kiem = ns["_e2b_kiem_noi_dung"]
loi = 0


def ca(ten, dk, them=""):
    global loi
    print(("  ĐẠT   " if dk else "  HỎNG  ") + ten + (f" — {str(them)[:220]}" if them != "" else "")); loi += not dk


def thu(ten, noi_dung):
    try:
        kiem(ten, noi_dung); return "qua"
    except ValueError as e:
        return "chan: " + str(e)


def vi(r: str, chu: str) -> bool:   # bản ĐANG GẮN từ chối hai đuôi mới ngay ở rào loại file ⇒ ca [ÂM] chỉ đòi "bị chặn"; bản MỚI đòi chặn ĐÚNG LÝ DO
    return r.startswith("chan") and (CU or chu in r)


def nen(cac_phan: dict, mat_khau=False) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for t, nd in cac_phan.items():
            z.writestr(t, nd)
    return b.getvalue()


def u16(b: bytes) -> bytes:   # chữ ASCII ⇒ UTF-16LE (mỗi ký tự kèm một byte 0) — cách sổ .xlsb lưu chuỗi trong phần .bin
    return b.decode("ascii").encode("utf-16-le")


print(f"== thử nới đuôi .html + .xlsb lên máy ảo E2B — bản {'ĐANG GẮN (cũ)' if CU else 'MỚI'}: {F} {hashlib.sha256(F.read_bytes()).hexdigest()[:8]}")
os.environ["CHAY_THU_E2B_DS"] = os.devnull            # danh sách file giả lập rỗng ⇒ mọi file đi nhánh "file người dùng"
os.environ["CHAY_THU_E2B_FILE"] = "nguoi_dung"; os.environ["CHAY_THU_E2B_MANG"] = "tat"

html_thuong = ("<html><head><meta charset='utf-8'><title>Tracking KPI</title></head><body><table><tr><td>Tuyến</td><td>Chỉ tiêu</td></tr>"
               "<tr><td>T01</td><td>120</td></tr></table></body></html>").encode("utf-8")
html_u16 = b"\xff\xfe" + "<html><body><table><tr><td>Tuyến</td><td>120</td></tr></table></body></html>".encode("utf-16-le")
xlsb_thuong = nen({"[Content_Types].xml": "<Types/>", "xl/workbook.bin": b"\x83\x01\x00\x80\x01\x00" + u16(b"Sheet1"),
                   "xl/sharedStrings.bin": b"\x9f\x01\x08\x02\x00\x00\x00" + u16(b"Tuyen ban hang") + b"\x00\x00" + u16(b"Chi tieu"),
                   "xl/worksheets/sheet1.bin": b"\x81\x01\x00" + b"\x00" * 64})

# ---- [ĐỎ TRƯỚC] hai đuôi mới: bản đang gắn từ chối, bản mới cho qua
for ten, nd in (("Tracking KPI Lam Phuong.html", html_thuong), ("TRANG.HTML", html_thuong), ("trang_luu_tu_excel.html", html_u16), ("So theo doi thang 10.xlsb", xlsb_thuong), ("SO.XLSB", xlsb_thuong)):
    r = thu(ten, nd)
    ca(f"D1 [ĐỎ TRƯỚC] '{ten}' nội dung thường ⇒ QUA rào đuôi", r == "qua", r)

# ---- [ÂM] đuôi lạ khác vẫn bị từ chối — KHÔNG nới rộng hơn hai đuôi được duyệt
for ten in ("trang.htm", "trang.xhtml", "trang.mhtml", "trang.mht", "anh.svg", "bang.xlsm", "bang.xls", "bang.xltx", "ma.py", "goi.zip", "du_lieu.json", "anh.png", "kich_ban.js",
            "khong_duoi", "trang.html.exe", "so.xlsb.zip", "trang.html.", "so.xlsb;.py", "html", ".html", ".xlsb", "x.docm", "lenh.sh", "trang.shtml", "trang.hta", "trang.html5"):
    r = thu(ten, html_thuong)
    ca(f"A1 [ÂM] loại không được duyệt '{ten}' ⇒ CHẶN", r.startswith("chan"), r)

# ---- [ÂM] dò khoá vẫn chặn ở hai đuôi mới — mẫu khoá GHÉP lúc chạy
pem = b"-----BEGIN " + b"RSA PRIVATE " + b"KEY-----\nMIIabc\n"
aws = b"khoa: " + b"AK" + b"IA" + b"ABCDEFGHIJKLMNOP" + b" het\n"
sk = b"OPENAI=" + b"s" + b"k-" + b"a1b2c3d4e5f6g7h8i9j0k1l2m3" + b"\n"
ghp = b"g" + b"hp_" + b"A" * 36
e2b = b"e" + b"2b_" + b"0123456789abcdef0123456789abcdef"
asak = b"aws_secret_" + b"access_key = xyz"
MAU = (("khối khoá riêng PEM", pem), ("khoá AWS", aws), ("khoá dạng sk-", sk), ("khoá GitHub", ghp), ("khoá E2B", e2b), ("dòng aws_secret_access_key", asak))
for nhan, nd in MAU:
    r = thu("trang.html", b"<html><body><!-- " + nd + b" --><p>bang</p></body></html>")
    ca(f"A2 [ÂM] .html có {nhan} (chữ thường trong trang) ⇒ CHẶN bởi dò khoá", vi(r, "giống KHOÁ"), r[:130])
    r = thu("trang.html", b"\xff\xfe" + u16(b"<html><body><p>") + u16(nd.replace(b"\n", b" ")) + u16(b"</p></body></html>"))
    ca(f"A3 [ÂM] .html lưu dạng UTF-16 có {nhan} ⇒ CHẶN (dò trên bản bỏ byte 0)", vi(r, "giống KHOÁ"), r[:130])
    r = thu("so.xlsb", nen({"[Content_Types].xml": "<Types/>", "xl/sharedStrings.bin": b"\x9f\x01\x08" + u16(b"ghi chu ") + u16(nd.replace(b"\n", b" ")) + b"\x00\x00"}))
    ca(f"A4 [ÂM] .xlsb có {nhan} trong sharedStrings.bin (UTF-16) ⇒ CHẶN, lời nói rõ 'bên trong'", vi(r, "bên trong"), r[:130])
    r = thu("so.xlsb", nen({"[Content_Types].xml": "<Types/>", "docProps/app.xml": b"<Properties><Company>" + nd + b"</Company></Properties>"}))
    ca(f"A5 [ÂM] .xlsb có {nhan} dạng chữ thường trong một phần XML ⇒ CHẶN", vi(r, "bên trong"), r[:130])
r = thu("so.xlsb", b"day khong phai file nen, chi la chu doi duoi")
ca("A6 [ÂM] .xlsb KHÔNG mở được như file nén ⇒ CHẶN (không soát được thì không gửi)", vi(r, "không mở được"), r[:140])
bom = io.BytesIO()
with zipfile.ZipFile(bom, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("xl/a.bin", b"\0" * 160_000_000)
r = thu("to.xlsb", bom.getvalue())
ca("A7 [ÂM] .xlsb phồng quá trần khi giải nén (160 MB, trần 150 MB) ⇒ CHẶN, không giải nén hết vào bộ nhớ", vi(r, "không soát hết"), (len(bom.getvalue()), r[:120]))
del bom
for ten in ("credentials.html", ".env.html", "api_key.xlsb", "id_rsa.html", "mat_khau_password.xlsb", "secrets.html", "token.xlsb"):
    r = thu(ten, xlsb_thuong if ten.endswith(".xlsb") else html_thuong)
    ca(f"A8 [ÂM] tên giống file khoá '{ten}' ⇒ CHẶN theo TÊN (rào tên không nới)", vi(r, "giống file khoá"), r[:110])
ca("A9 [ÂM] lời từ chối KHÔNG chép nội dung file", "MIIabc" not in thu("trang.html", b"<p>" + pem + b"</p>") and "ABCDEFGHIJKLMNOP" not in thu("so.xlsb", nen({"a.bin": u16(aws.strip())})))
r = [thu("trang.html", b"<p>ma sk-1, AKIA ngan, e2b_x, BEGIN KEY</p>"), thu("so.xlsb", nen({"xl/sharedStrings.bin": u16(b"ma sk-1, AKIA ngan, e2b_x") + bytes(range(256)) * 40}))]
ca("D2 [ĐỎ TRƯỚC] .html / .xlsb nội dung thường có chữ gần giống khoá / phần nhị phân đủ 256 giá trị byte ⇒ KHÔNG chặn oan", r == ["qua", "qua"], r)

r = [thu("trang.html ", html_thuong), thu(" so.xlsb", xlsb_thuong), thu("a.csv ", b"a,b\n1,2\n")]
ca("D3 [ĐỎ TRƯỚC] tên có dấu cách đầu / cuối được làm sạch rồi mới xét đuôi (hành vi SẴN CÓ, 'a.csv ' cũng vậy) ⇒ .html / .xlsb qua", r == ["qua"] * 3, r)

# ---- các đuôi cũ không đổi hành vi (rào không nới / không siết ngoài hai đuôi mới)
that = "mã đơn,số tiền\nĐ1,250000\n".encode(); that_nen = nen({"[Content_Types].xml": "<Types/>", "xl/sharedStrings.xml": "<sst><si><t>đơn hàng</t></si></sst>"})
r = [thu("a.csv", that), thu("a.txt", that), thu("a.md", that), thu("a.pdf", b"%PDF-1.7\n" + that), thu("a.xlsx", that_nen), thu("a.docx", that_nen), thu("a.pptx", that_nen)]
ca("G1 bảy đuôi cũ, nội dung thường ⇒ vẫn QUA", r == ["qua"] * 7, r)
r = [thu("a.xlsx", nen({"xl/sharedStrings.xml": b"<t>" + aws + b"</t>"})), thu("a.txt", b"x\n" + sk), thu("a.xlsx", that)]
ca("G2 đuôi cũ: khoá trong .xlsx / .txt vẫn CHẶN; .xlsx không phải file nén vẫn CHẶN", all(x.startswith("chan") for x in r), [x[:60] for x in r])
# ---- [VÒNG 2 — ĐỎ TRƯỚC] phần SIẾT cho đuôi cũ: khoá viết UTF-16 / UTF-32 (lỗ có sẵn ở bản đang gắn — ca G3 vòng 1, phiên kiểm xác nhận ca N10)
for nhan, nd in MAU:
    k = nd.replace(b"\n", b" ")
    r = [thu("a.txt", b"\xff\xfe" + u16(b"x " + k + b" y")), thu("a.csv", u16(b"cot1,cot2\n1," + k + b"\n")), thu("a.md", b"\xfe\xff" + (b"x " + k + b" y").decode("ascii").encode("utf-16-be")),
         thu("a.txt", (b"x " + k + b" y").decode("ascii").encode("utf-32-le")), thu("a.xlsx", nen({"[Content_Types].xml": "<Types/>", "xl/a.bin": u16(k)}))]
    ca(f"U1 [ĐỎ TRƯỚC] đuôi cũ, {nhan} viết UTF-16LE (.txt, .csv) / UTF-16BE (.md) / UTF-32 (.txt) / trong phần nhị phân của .xlsx ⇒ CHẶN cả 5", all(x.startswith("chan") and (CU or "KHOÁ" in x) for x in r), [x[:14] for x in r])

# ---- [VÒNG 2 — major N7 của phiên kiểm] sổ .xlsb lưu chuỗi = SỐ ĐO ĐỘ DÀI rồi chữ UTF-16LE; số đo có thể là byte chữ / số dính ngay trước khoá
def o_sk(dai):   # ô chứa ĐÚNG một khoá kiểu sk- dài `dai` ký tự
    return (b"s" + b"k-" + (b"a1B2c3D4e5" * 20)[:dai - 3]).decode()
def o_akia(dai):   # khoá AWS đứng ĐẦU một ô dài `dai` ký tự
    return ((b"AK" + b"IA" + b"ABCDEFGHIJKLMNOP").decode() + " ghi chu them cho du do dai " * 8)[:dai]
def sst(chuoi, kieu="I"):   # kieu: "I" = 4 byte độ dài (chuỗi của ô / chuỗi dùng chung) · "H" = 2 byte (chuỗi trong công thức) · "cI" = 1 byte cờ + 4 byte độ dài
    dau = {"I": struct.pack("<I", len(chuoi)), "H": b"\x17" + struct.pack("<H", len(chuoi)), "cI": b"\x01" + struct.pack("<I", len(chuoi))}[kieu]
    return nen({"[Content_Types].xml": "<Types/>", "xl/sharedStrings.bin": b"\x9f\x01\x08\x02\x00\x00\x00\x13\x05" + dau + chuoi.encode("utf-16-le") + b"\x13\x09\x00"})
for kieu, ten_kieu in (("I", "4 byte độ dài"), ("H", "2 byte độ dài (chuỗi trong công thức)"), ("cI", "1 byte cờ + 4 byte độ dài")):
    lot = [d for d in range(23, 131) if not thu("so.xlsb", sst(o_sk(d), kieu)).startswith("chan")]
    ca(f"N7a [ÂM — vòng 1 LỌT ở độ dài 48–57, 65–90, 95, 97–122] .xlsb, ô chứa đúng một khoá sk- dài 23…130 ký tự, {ten_kieu} ⇒ CHẶN ở MỌI độ dài", not lot, f"lọt ở độ dài: {lot[:30]}")
    lot = [d for d in range(20, 131) if not thu("so.xlsb", sst(o_akia(d), kieu)).startswith("chan")]
    ca(f"N7b [ÂM] .xlsb, khoá AWS đứng đầu một ô dài 20…130 ký tự, {ten_kieu} ⇒ CHẶN ở MỌI độ dài", not lot, f"lọt ở độ dài: {lot[:30]}")
lot = [d for d in range(23, 131) if not thu("a.xlsx", nen({"xl/a.bin": struct.pack("<I", d) + o_sk(d).encode("utf-16-le")})).startswith("chan")]
ca("N7c [ĐỎ TRƯỚC] cùng cách lưu chuỗi đó nằm trong phần nhị phân của .xlsx (đuôi cũ) ⇒ CHẶN ở mọi độ dài", not lot, f"lọt ở độ dài: {lot[:30]}")
r = thu("so.xlsb", nen({"xl/sharedStrings.bin": b"\x00" * (8 * 1024 * 1024 - 30) + struct.pack("<I", 51) + o_sk(51).encode("utf-16-le") + b"\x00" * 5000}))
ca("N7d [ÂM] khoá nằm VẮT ranh giới hai đoạn 8 MB của lượt dò phụ ⇒ vẫn CHẶN", vi(r, "bên trong"), r[:110])

# ---- [VÒNG 2 — ca chỉ MỘT cách nhìn bắt được; có được nhờ chạy đột biến: bỏ từng cách nhìn mà bộ thử vẫn xanh thì thêm ca]
k51 = o_sk(51).encode("utf-16-le")
r = thu("so.xlsb", nen({"xl/a.bin": b"\x00\x00AB" + struct.pack("<I", 51) + k51 + b"\x00\x00"}))
ca("N7e [ÂM] trước số đo độ dài là HAI byte chữ (bỏ byte 0 thì ba ký tự chữ / số dính trước khoá) ⇒ vẫn CHẶN nhờ cách nhìn giải mã UTF-16 độ lệch chẵn", vi(r, "bên trong"), r[:100])
r = thu("so.xlsb", nen({"xl/a.bin": b"\x00\x00ABC" + struct.pack("<I", 51) + k51 + b"\x00\x00"}))
ca("N7f [ÂM] như trên nhưng chuỗi nằm ở vị trí LẺ ⇒ vẫn CHẶN nhờ cách nhìn giải mã UTF-16 độ lệch lẻ", vi(r, "bên trong"), r[:100])
r = thu("so.xlsb", nen({"xl/a.bin": "\u4142\u4344".encode("utf-16-le") + k51 + b"\x00\x00"}))
ca("N7g [ÂM] khoá đứng ngay sau hai chữ ngoài ASCII mà cả hai byte đều là byte chữ ⇒ vẫn CHẶN (giải mã thấy đó không phải chữ ASCII)", vi(r, "bên trong"), r[:100])
r = [thu("a.txt", b"\xff\xfe" + u16(b"ghi chu task-force-" + o_sk(51).encode() + b" het")), thu("so.xlsb", sst("task-force-" + o_sk(51)))]
ca("N7h [ĐỎ TRƯỚC] khoá đứng sau dấu gạch nối của một cụm chữ thường ('task-force-<khoá>') viết UTF-16 ⇒ CHẶN (không nhảy qua cả cụm vừa bị bỏ qua)", all(x.startswith("chan") for x in r), [x[:40] for x in r])

# ---- [VÒNG 2] lượt dò phụ KHÔNG chặn oan chữ thường có gạch nối / chuỗi thường có số đo độ dài đứng trước
thuong = ("tas" + "k-force-on-climate-related-financial-disclosures", "ris" + "k-adjusted-return-on-capital-employed-2026", "des" + "k-research-notes-for-quarter-four-planning", "Tuyen ban hang so 12 - khu vuc mien Tay",
          "mas" + "k-rcnn-resnet50-fpn-coco-pretrained-weights", "AKIAN KHANG GROUP JSC", "sk-1", "e2b_x ghp_y")
r = [thu("a.txt", b"\xff\xfe" + u16(x.encode())) for x in thuong] + [thu("so.xlsb", sst(x)) for x in thuong] + [thu("trang.html", b"\xff\xfe" + u16(b"<td>" + x.encode() + b"</td>")) for x in thuong]
ca("O1 chữ thường viết UTF-16 có 'task-…', 'risk-…', 'desk-…', 'mask-…' dài + chữ gần giống khoá, ở .txt / .xlsb / .html ⇒ QUA cả (24 ca)" + (" [bản cũ: .txt qua, hai đuôi mới bị chặn theo loại]" if CU else ""),
   (r[:8] == ["qua"] * 8) if CU else (r == ["qua"] * 24), [i for i, x in enumerate(r) if x != "qua"])
dai = ("AK" + "IA") + "ABCDEFGHIJKLMNOPQRSTUVWX"      # một mã dài liền 28 ký tự bắt đầu bằng bốn chữ đó — mẫu chính cũng KHÔNG coi là khoá (sau 16 ký tự không có ranh giới)
r = [thu("a.txt", dai.encode()), thu("a.txt", b"\xff\xfe" + u16(b"ma hang " + dai.encode() + b" het")), thu("so.xlsb", sst("ma hang " + dai))]
ca("O3 mã dài liền bắt đầu bằng bốn chữ đầu của khoá AWS (không phải khoá) viết thường / UTF-16 ⇒ QUA" + (" [bản cũ: hai ca .txt]" if CU else ""), (r[:2] == ["qua"] * 2) if CU else (r == ["qua"] * 3), r)
lot = [d for d in range(1, 131) if thu("so.xlsb", sst(("Tuyen ban hang khu vuc mien Tay Nam Bo " * 5)[:d])) != "qua"]
ca("O2 [ĐỎ TRƯỚC] .xlsb, ô chữ thường dài 1…130 ký tự (mọi giá trị byte độ dài) ⇒ QUA ở mọi độ dài", not lot, f"chặn oan ở độ dài: {lot[:30]}")
os.environ["CHAY_THU_E2B_FILE"] = "gia_lap"
r = [thu("trang.html", html_thuong), thu("so.xlsb", xlsb_thuong)]
ca("G4 [ÂM] công tắc gia_lap (không phải nguoi_dung): .html / .xlsb của người dùng vẫn CHẶN", all(x.startswith("chan") for x in r), [x[:70] for x in r])
os.environ["CHAY_THU_E2B_FILE"] = "bat-het"
r = [thu("trang.html", html_thuong), thu("so.xlsb", xlsb_thuong)]
ca("G5 [ÂM] công tắc giá trị LẠ ⇒ vẫn CHẶN (đóng khi lỗi)", all(x.startswith("chan") for x in r), [x[:70] for x in r])
os.environ["CHAY_THU_E2B_FILE"] = "nguoi_dung"

# ---- [ÂM] trần 20.000.000 byte: thử ĐÚNG hàm _kiem_file_vao (ba hàm nó gọi sang thay bằng bản giả) + dòng gọi trong sandbox_runtime không đổi
V1 = CT / "sua_doi/services/pydantic_ai_skills/e2b_chat_service.py"
ham = next(n for n in ast.parse(V1.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef) and n.name == "_kiem_file_vao")
gia = types.ModuleType("nodes.agent.native_tools"); gia.TRAN_FILE_TAI = 3_000_000; gia._hoi_co_s3 = lambda nf: nf.co; gia.kiem_khoa_cua_to_chuc = lambda k, o: None
for t in ("nodes", "nodes.agent"):
    sys.modules.setdefault(t, types.ModuleType(t))
sys.modules["nodes.agent.native_tools"] = gia
from typing import Any
ns2 = {"Any": Any}; exec(compile(ast.Module(body=[ham], type_ignores=[]), str(V1), "exec"), ns2)


def cua(ten, co):
    try:
        ns2["_kiem_file_vao"](types.SimpleNamespace(name=ten, co=co, s3_key="k", generated=False), "org", ns.get("_E2B_TRAN_FILE_NGUOI_DUNG")); return "qua"
    except ValueError as e:
        return "chan: " + str(e)


r = {(t, c): cua(t, c) for t in ("trang.html", "so.xlsb") for c in (20_000_000, 20_000_001, 150_000_000)}
ca("T1 [ÂM] trần cỡ: .html / .xlsb đúng 20.000.000 byte qua; 20.000.001 và 150.000.000 byte ⇒ CHẶN 'quá trần' (trần không theo đuôi)",
   ns.get("_E2B_TRAN_FILE_NGUOI_DUNG") == 20_000_000 and all((v == "qua") == (k[1] == 20_000_000) and (v == "qua" or "quá trần" in v) for k, v in r.items()), {f"{k[0]}:{k[1]}": v[:50] for k, v in r.items()})
ca("T2 dòng áp trần + dòng gọi rào nội dung trong sandbox_runtime còn nguyên (đọc mã nguồn)",
   re.search(r'tran_e2b = _E2B_TRAN_FILE_NGUOI_DUNG if \(self\._e2b_dung\(\) and _e2b_che_do_file\(\) == "nguoi_dung"\) else None', src) is not None
   and src.count("_e2b_kiem_noi_dung(os.path.basename(nf.name)") == 2 and "kiem_file_vao(nf, getattr(self, \"_orgid\", None), tran_e2b)" in src)

# ---- phạm vi sửa: chỉ đoạn hàm rào; danh sách của các đường khác không nằm trong gói
if not CU and not DOI:
    goc = (GOC if GOC.exists() else CT / "sua_doi/workflow/node/base/agent/sandbox_runtime.py").read_text(encoding="utf-8")
    cat = lambda s: (s[:s.index("def _e2b_ds_cho_phep")], s[s.index("class _E2bChayThu"):])
    ca("P1 bản mới CHỈ khác bản gốc trong đoạn hàm rào file (trước `def _e2b_ds_cho_phep` và từ `class _E2bChayThu` trở đi giống từng ký tự)", cat(src) == cat(goc))
    ca("P2 gói chỉ có MỘT file mã gắn vào bộ (sandbox_runtime.py) — không chạm may_chu.py / kho_tai_lieu.py / upload_allowlist.py",
       sorted(p.name for p in GOI.iterdir() if p.suffix == ".py" and not p.name.startswith("thu_") and p.name != "dat_file_do.py") == ["sandbox_runtime.py"])
    ca("P4 lượt dò phụ chỉ chạy khi nội dung có byte 0 (đọc mã nguồn) ⇒ file chữ thường / UTF-8 đi đúng đường cũ", 'if b"\\x00" not in b:' in src and "return False" in src[src.index('if b"\\x00" not in b:'):][:260])
    ca("P3 danh sách mới = danh sách cũ + ĐÚNG hai đuôi", tuple(ns["_E2B_DUOI_NGUOI_DUNG"]) == (".csv", ".xlsx", ".pdf", ".docx", ".pptx", ".txt", ".md", ".html", ".xlsb"), ns["_E2B_DUOI_NGUOI_DUNG"])
print(f"== KẾT: {loi} ca hỏng"); sys.exit(1 if loi else 0)
