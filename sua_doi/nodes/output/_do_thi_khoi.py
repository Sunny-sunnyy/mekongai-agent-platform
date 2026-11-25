"""[ghi chú] Khối đồ thị quan hệ
(network_graph_output_node) VẼ BẰNG CODE: gpt-6-luna chỉ trả JSON nút / cạnh có khuôn ⇒ code kiểm ⇒ code tính bố cục ⇒ code dựng HTML (SVG) từ
KHUÔN CỐ ĐỊNH ⇒ tải lên bucket outputs (MinIO trên máy) với khoá có mã tổ chức ⇒ NodeOutput như gốc. Gắn: /srv/src/nodes/output/_do_thi_khoi.py.

Gốc Capitol [code, agentic-backend release-4.10.0]: NetworkGraphNode dựng E2BSkillsChatService (nodes/output/org_chart/node.py:60) ⇒ Claude VIẾT CODE
Python trong hộp E2B (có mạng mặc định) sinh graph.html tải D3 từ CDN; mọi hộp được nạp khoá bản đồ CARTO (D, NETWORK_GRAPH.md mục 1).
Bản thay: không chạy code AI viết, không hộp, không Claude, không CDN, không khoá nào trong file.
KHÁC D (đề xuất "nhúng D3 ghim sha"): máy không có sẵn D3 (tìm 09/11: chỉ có ví dụ d3-gallery) và tải về = lấy thêm dữ liệu cần người vận hành ⇒
KHÔNG dùng thư viện nào: bố cục tính bằng Python (Fruchterman–Reingold, hạt giống cố định), vẽ SVG tĩnh + đoạn JS nhỏ CỐ ĐỊNH (phóng / kéo /
sáng cạnh / xuất SVG / PNG). CSP: script / style chỉ đúng mã băm của khuôn; connect-src 'none'.
An toàn chữ: mọi nhãn qua html.escape(quote=True); mã nút trong HTML do CODE đặt (n0, n1, …) — không lấy mã do AI đưa; không dữ liệu nào nằm trong <script>.
Cờ: CHAY_THU_DO_THI_KHOI (mặc định BẬT; false / 0 / no / off ⇒ đúng đường Capitol gốc).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import math
import os
import random
import re
import unicodedata
from io import BytesIO
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
TRAN_NUT, TRAN_CANH = 300, 1000
THU_MUC_S3 = "graphs/network"   # như Capitol: services/org_chart/config.py:17
CT_HTML = "text/html; charset=utf-8"
MAU = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d", "#475569", "#ea580c"]


# [D m1 mục 59, 09/11] chữ từ AI ⇒ NFC + bỏ ký tự điều khiển (như xuat_file._sach / _CTRL, xuat_file.py:50) + ký tự ĐẢO CHIỀU chữ
# (U+202A–202E, U+2066–2069, U+200E/F) + nửa cặp surrogate lẻ (utf-8 không mã hoá được). Lý do: ký tự điều khiển không hợp lệ trong XML ⇒
# "Xuất SVG" (XMLSerializer) hỏng; đảo chiều làm nhãn hiện sai thứ tự. Xuống dòng / tab ⇒ dấu cách.
_BO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069\ud800-\udfff]")


# [gom minor 09/11 — D m2 mục 55 / 59] mã tổ chức đi vào khoá S3 ⇒ kiểm KHUÔN (chặn "/", "..", ký tự lạ — khoá không trèo sang thư mục khác)
_ORG = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _org_hop_le(orgid) -> bool:
    return isinstance(orgid, str) and bool(_ORG.fullmatch(orgid))


def _sach(x, dem: list) -> str:
    s = unicodedata.normalize("NFC", "" if x is None else str(x)).replace("\r\n", " ").replace("\n", " ").replace("\t", " ")
    s2 = _BO.sub("", s)
    dem[0] += len(s) - len(s2)
    return s2


def bat() -> bool:
    return os.environ.get("CHAY_THU_DO_THI_KHOI", "true").strip().lower() not in ("false", "0", "no", "off")


Chu = Annotated[str, Field(min_length=1, max_length=200)]


class Nut(BaseModel):
    id: Annotated[str, Field(min_length=1, max_length=100)] = Field(description="mã ngắn, duy nhất")
    nhan: Chu = Field(description="tên hiển thị")
    nhom: Annotated[str, Field(max_length=60)] | None = Field(None, description="nhóm / loại (tô màu)")


class Canh(BaseModel):
    nguon: Annotated[str, Field(min_length=1, max_length=100)]
    dich: Annotated[str, Field(min_length=1, max_length=100)]
    nhan: Annotated[str, Field(max_length=200)] | None = None
    trong_so: float | None = Field(None, ge=0, le=100, description="độ mạnh quan hệ 0–100 (tuỳ chọn)")


class DoThi(BaseModel):
    tieu_de: Chu
    nut: list[Nut] = Field(min_length=1, max_length=TRAN_NUT)
    canh: list[Canh] = Field(default_factory=list, max_length=TRAN_CANH)


SYS = (
    "Bạn TRÍCH đồ thị quan hệ từ yêu cầu / dữ liệu được đưa: các nút (người, tổ chức, khái niệm…) và các cạnh (quan hệ giữa hai nút).\n"
    "- Chỉ đưa thực thể và quan hệ CÓ trong dữ liệu; KHÔNG bịa người, tổ chức, quan hệ, con số. Thiếu thì để thiếu.\n"
    "- Mỗi nút một mã ngắn duy nhất (id); cạnh trỏ tới đúng id đó (nguon → dich). Nhãn ngắn; nhom dùng để tô màu (tuỳ chọn).\n"
    "- trong_so (0–100) chỉ ghi khi dữ liệu có thông tin về độ mạnh quan hệ.\n"
    "- Viết cùng ngôn ngữ với yêu cầu. Không viết đường dẫn (link), không viết code.\n"
)

# ---- khuôn HTML CỐ ĐỊNH (script / style là hằng ⇒ băm cố định, CSP chỉ cho đúng 2 băm này) ----
STYLE = (
    "body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f8fafc;color:#0f172a}"
    "header{padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #e2e8f0;background:#fff}"
    "h1{font-size:18px;margin:0;flex:1}button{border:1px solid #cbd5e1;background:#fff;border-radius:6px;padding:6px 10px;cursor:pointer}"
    "#g{width:100vw;height:calc(100vh - 110px);display:block;cursor:grab}.canh{stroke:#94a3b8;stroke-opacity:.7}"
    ".canh.sang{stroke:#0f172a;stroke-opacity:1}.nut text{font-size:12px;paint-order:stroke;stroke:#fff;stroke-width:3px}"
    ".cl{font-size:11px;fill:#475569}footer{padding:6px 16px;font-size:12px;color:#475569}.chu-giai span{margin-right:12px}"
    ".chu-giai i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:middle}"
)
SCRIPT = (
    "(function(){var s=document.getElementById('g'),v=document.getElementById('vp'),sc=1,tx=0,ty=0,dr=false,px=0,py=0;"
    "function ap(){v.setAttribute('transform','translate('+tx+','+ty+') scale('+sc+')');}"
    "s.addEventListener('wheel',function(e){e.preventDefault();sc=Math.min(8,Math.max(0.2,sc*(e.deltaY<0?1.1:1/1.1)));ap();},{passive:false});"
    "s.addEventListener('mousedown',function(e){dr=true;px=e.clientX;py=e.clientY;});"
    "window.addEventListener('mousemove',function(e){if(!dr)return;tx+=e.clientX-px;ty+=e.clientY-py;px=e.clientX;py=e.clientY;ap();});"
    "window.addEventListener('mouseup',function(){dr=false;});"
    "function tai(t,b){var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=t;document.body.appendChild(a);a.click();a.remove();}"
    "function svg(){return new Blob([new XMLSerializer().serializeToString(s)],{type:'image/svg+xml'});}"
    "document.getElementById('xs').addEventListener('click',function(){tai('do_thi.svg',svg());});"
    "document.getElementById('xp').addEventListener('click',function(){var i=new Image();i.onload=function(){var b=s.viewBox.baseVal,"
    "c=document.createElement('canvas');c.width=b.width*2;c.height=b.height*2;var x=c.getContext('2d');x.fillStyle='#fff';"
    "x.fillRect(0,0,c.width,c.height);x.drawImage(i,0,0,c.width,c.height);c.toBlob(function(p){tai('do_thi.png',p);});};"
    "i.src=URL.createObjectURL(svg());});"
    "document.querySelectorAll('.nut').forEach(function(n){var id=n.getAttribute('data-id');"
    "n.addEventListener('mouseenter',function(){document.querySelectorAll('.canh').forEach(function(c){"
    "c.classList.toggle('sang',c.getAttribute('data-a')===id||c.getAttribute('data-b')===id);});});"
    "n.addEventListener('mouseleave',function(){document.querySelectorAll('.canh').forEach(function(c){c.classList.remove('sang');});});});"
    "})();"
)


def _bam(s: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode() + "'"


CSP = (f"default-src 'none'; script-src {_bam(SCRIPT)}; style-src {_bam(STYLE)}; img-src data: blob:; connect-src 'none'; "
       "font-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'")


def kiem(dt: DoThi) -> tuple[list[dict], list[tuple[int, int, str | None, float | None]], list[str]]:
    """Kiểm bằng code: mã nút trùng ⇒ giữ nút đầu; cạnh trỏ nút không có / tự nối ⇒ bỏ + ĐẾM; cạnh trùng ⇒ gộp. Trả (nút, cạnh theo chỉ số, cảnh báo)."""
    canh_bao, chi_so, nut, bo = [], {}, [], [0]
    for n in dt.nut:
        if n.id in chi_so:
            continue
        chi_so[n.id] = len(nut)
        nut.append({"nhan": _sach(n.nhan, bo) or "(trống)", "nhom": _sach(n.nhom, bo).strip() or None})
    if len(nut) < len(dt.nut):
        canh_bao.append(f"bỏ {len(dt.nut) - len(nut)} nút trùng mã")
    canh, da, la, tu = [], set(), 0, 0
    for c in dt.canh:
        a, b = chi_so.get(c.nguon), chi_so.get(c.dich)
        if a is None or b is None:
            la += 1
            continue
        if a == b:
            tu += 1
            continue
        k = (min(a, b), max(a, b), c.nhan or "")
        if k in da:
            continue
        da.add(k)
        canh.append((a, b, _sach(c.nhan, bo) or None, c.trong_so))
    if bo[0]:
        canh_bao.append(f"bỏ {bo[0]} ký tự điều khiển / đảo chiều trong nhãn")
    if la:
        canh_bao.append(f"bỏ {la} cạnh trỏ tới nút không có")
    if tu:
        canh_bao.append(f"bỏ {tu} cạnh tự nối")
    return nut, canh, canh_bao


def bo_cuc(n: int, canh, w=1000.0, h=700.0, vong=160) -> list[tuple[float, float]]:
    """Fruchterman–Reingold, hạt giống cố định ⇒ cùng dữ liệu cùng hình. n > 150 ⇒ xếp vòng (giữ thời gian chạy ngắn)."""
    if n == 1:
        return [(w / 2, h / 2)]
    if n > 150:
        return [(w / 2 + 0.45 * w * math.cos(2 * math.pi * i / n), h / 2 + 0.45 * h * math.sin(2 * math.pi * i / n)) for i in range(n)]
    rd = random.Random(42)
    p = [[rd.uniform(0.1, 0.9) * w, rd.uniform(0.1, 0.9) * h] for _ in range(n)]
    k = math.sqrt(w * h / n)
    t = w / 10
    for _ in range(vong):
        d = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                dx, dy = p[i][0] - p[j][0], p[i][1] - p[j][1]
                kc = math.hypot(dx, dy) or 0.01
                f = k * k / kc
                d[i][0] += dx / kc * f; d[i][1] += dy / kc * f
                d[j][0] -= dx / kc * f; d[j][1] -= dy / kc * f
        for a, b, *_ in canh:
            dx, dy = p[a][0] - p[b][0], p[a][1] - p[b][1]
            kc = math.hypot(dx, dy) or 0.01
            f = kc * kc / k
            d[a][0] -= dx / kc * f; d[a][1] -= dy / kc * f
            d[b][0] += dx / kc * f; d[b][1] += dy / kc * f
        for i in range(n):
            dd = math.hypot(*d[i]) or 0.01
            p[i][0] = min(w - 40, max(40, p[i][0] + d[i][0] / dd * min(dd, t)))
            p[i][1] = min(h - 30, max(30, p[i][1] + d[i][1] / dd * min(dd, t)))
        t *= 0.97
    return [(x, y) for x, y in p]


def dung_html(tieu_de: str, nut: list[dict], canh, canh_bao: list[str]) -> str:
    e = lambda s: html.escape(str(s), quote=True)   # noqa: E731 — MỌI chữ từ AI đi qua đây
    pos = bo_cuc(len(nut), canh)
    nhom = sorted({x["nhom"] for x in nut if x["nhom"]})
    mau = {g: MAU[i % len(MAU)] for i, g in enumerate(nhom)}
    ds = []
    for a, b, nh, ts in canh:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        rong = 1 + (ts or 0) / 25
        ds.append(f'<line class="canh" data-a="n{a}" data-b="n{b}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke-width="{rong:.2f}"/>')
        if nh:
            ds.append(f'<text class="cl" x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2:.1f}" text-anchor="middle">{e(nh)}</text>')
    for i, x in enumerate(nut):
        cx, cy = pos[i]
        ds.append(f'<g class="nut" data-id="n{i}"><circle cx="{cx:.1f}" cy="{cy:.1f}" r="9" fill="{mau.get(x["nhom"], "#64748b")}">'
                  f'<title>{e(x["nhan"])}{(" — " + e(x["nhom"])) if x["nhom"] else ""}</title></circle>'
                  f'<text x="{cx + 12:.1f}" y="{cy + 4:.1f}">{e(x["nhan"])}</text></g>')
    chu_giai = "".join(f'<span><i class="m{i}"></i>{e(g)}</span>' for i, g in enumerate(nhom))
    style = STYLE + "".join(f".m{i}{{background:{MAU[i % len(MAU)]}}}" for i in range(len(nhom)))   # màu chú giải: hằng theo chỉ số
    return ("<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP.replace(_bam(STYLE), _bam(style))}\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{e(tieu_de)}</title><style>{style}</style></head><body>"
            f"<header><h1>{e(tieu_de)}</h1><button id=\"xs\" type=\"button\">Xuất SVG</button><button id=\"xp\" type=\"button\">Xuất PNG</button></header>"
            f"<svg id=\"g\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 1000 700\"><g id=\"vp\">{''.join(ds)}</g></svg>"
            f"<footer><div class=\"chu-giai\">{chu_giai}</div>{len(nut)} nút · {len(canh)} cạnh"
            f"{(' · ' + e('; '.join(canh_bao))) if canh_bao else ''} · vẽ bằng code (chay_thu), không chạy code AI viết</footer>"
            f"<script>{SCRIPT}</script></body></html>")


# [ghi chú]
# Trần CỨNG để giữ chỗ được (mức ở muc_toi_da.py DO_THI_*). Cắt theo BYTE UTF-8 (token ≤ byte — D minor 1), không theo ký tự.
DO_THI_YEU_CAU_BYTE, DO_THI_DAN_THEM_BYTE = 40_000, 8_000
DO_THI_LAN_GOI, DO_THI_TRAN_VAO, DO_THI_TRAN_RA, DO_THI_RA_MOT_LAN = 3, 120_000, 12_000, 4_000   # 3 = 1 lần + retries=2 (sửa khuôn đầu ra)


def _cat_byte(s: str, toi_da: int) -> tuple[str, bool]:
    b = s.encode("utf-8")
    if len(b) <= toi_da:
        return s, False
    return b[:toi_da].decode("utf-8", errors="ignore"), True


async def _soan(he_thong: str, yeu_cau: str, orgid: str):
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits

    from ai.agentic.models.openai import get_openai_model_for_agent_node
    from core.config.ai.openai import OpenAIModels
    from services.provider_keys import Provider, resolve_api_key_for_provider

    agent = Agent(model=get_openai_model_for_agent_node(OpenAIModels.GPT_6_LUNA, api_key=await resolve_api_key_for_provider(Provider.OPENAI, orgid)),
                  system_prompt=he_thong, output_type=DoThi, retries=2)
    # request_limit tính cả lần sửa khuôn đầu ra (retries=2); trần vào / ra là TỔNG đã dùng (kiểm trước lần gọi với vào, sau với ra)
    r = await agent.run(yeu_cau, model_settings={"max_tokens": DO_THI_RA_MOT_LAN},
                        usage_limits=UsageLimits(request_limit=DO_THI_LAN_GOI, input_tokens_limit=DO_THI_TRAN_VAO,
                                                 output_tokens_limit=DO_THI_TRAN_RA))
    return r.output, json.loads(r.all_messages_json())


async def _tai_len(data: bytes, orgid: str) -> dict:
    from core.settings import settings
    from services.aws.s3 import S3Service

    bucket = settings.OUTPUTS_BUCKET_NAME
    key = f"{THU_MUC_S3}/{orgid}/{uuid4()}.html"   # mã tổ chức của LƯỢT (D S6 / F1) — Capitol gốc không có (e2b_chat_service.py:1333)
    s3 = S3Service()
    # [D m3] HTML ⇒ tải về, không hiện thẳng trên miền MinIO: Content-Disposition attachment cả lúc ghi lẫn trong link
    await asyncio.to_thread(s3.upload_file, file=BytesIO(data), bucket_name=bucket, key=key,
                            extras={"ContentType": CT_HTML, "ContentDisposition": 'attachment; filename="do_thi.html"'})
    url = await asyncio.to_thread(s3.generate_presigned_url, s3_key=key, bucket_name=bucket, filename="do_thi.html", content_type=CT_HTML,
                                  download=True)
    return {"filename": "do_thi.html", "s3_url": url, "s3_bucket": bucket, "s3_key": key, "s3_region": s3.region or "us-east-1", "content_type": CT_HTML}


async def chay_khoi(node):
    """Nhánh thay cho NetworkGraphNode.execute() gốc. Sự kiện như gốc: begin / progress / success / node_result / output / end; lỗi ⇒ error + end."""
    from sentry_sdk import capture_exception

    from core.settings import settings
    from nodes.grounding import apply_grounding
    from workflow.node.base.node import NodeOutput
    from workflow.node.base.param.custom import MarkdownDisplay

    canh_bao: list[str] = []
    try:
        await node.emit_begin()
        node._validated_params = await node._get_validated_params()
        p = node._validated_params
        yeu_cau = node._get_user_prompt()
        if not yeu_cau.strip():
            raise ValueError("khối không có yêu cầu (ô task trống)")
        if not node._orgid:   # kiểm chỗ ghi TRƯỚC khi gọi AI (bài học MỤC 55, K4)
            raise ValueError("không có mã tổ chức của lượt — không ghi file ra")
        if not _org_hop_le(node._orgid):   # D m2
            raise ValueError("mã tổ chức của lượt sai khuôn — không ghi file ra")
        if not settings.OUTPUTS_BUCKET_NAME:
            raise ValueError("OUTPUTS_BUCKET_NAME trống — không ghi file ra")
        files = await node._extract_input_files()
        if files:
            canh_bao.append(f"Khối nhận {len(files)} file nhưng bản thay chay_thu CHƯA đọc nội dung file — chỉ dùng chữ của yêu cầu.")
        yeu_cau, cat = _cat_byte(yeu_cau, DO_THI_YEU_CAU_BYTE)   # [va_muc_toi_da_r2] trần đầu vào cho mức giữ chỗ
        if cat:
            canh_bao.append(f"Yêu cầu dài quá {DO_THI_YEU_CAU_BYTE} byte — đã cắt phần cuối.")
        he_thong = SYS
        if isinstance(p.get("system_prompt"), str) and p["system_prompt"].strip():
            dan, cat = _cat_byte(p["system_prompt"].strip(), DO_THI_DAN_THEM_BYTE)
            if cat:
                canh_bao.append(f"Dặn thêm dài quá {DO_THI_DAN_THEM_BYTE} byte — đã cắt phần cuối.")
            he_thong += f"\n## Dặn thêm của người dùng\n{dan}"
        he_thong = apply_grounding(he_thong, p.get("ground_with_provided_info", False))
        for c in canh_bao:
            await node.emit_progress(dynamic_status=c)
        await node.emit_progress(dynamic_status="Đang trích nút / cạnh (gpt-6-luna) …")
        dt, lich_su = await _soan(he_thong, yeu_cau, node._orgid)
        nut, canh, cb = kiem(dt)
        canh_bao += cb
        bo = [0]
        tieu_de = _sach(dt.tieu_de, bo) or "Đồ thị quan hệ"
        if bo[0]:
            canh_bao.append(f"bỏ {bo[0]} ký tự điều khiển / đảo chiều trong tiêu đề")
        noi_dung = dung_html(tieu_de, nut, canh, canh_bao).encode("utf-8")
        tai = await _tai_len(noi_dung, node._orgid)
        node_files = node._create_output_node_files([tai])
        nhom = sorted({x["nhom"] for x in nut if x["nhom"]})
        tom = (f"**Đồ thị quan hệ** — {len(nut)} nút, {len(canh)} cạnh, {len(nhom)} nhóm. File: do_thi.html"
               + (f"\n\nCảnh báo: {'; '.join(canh_bao)}" if canh_bao else ""))
        output = NodeOutput(
            files=node_files, data=tom, node=node,
            display_model=MarkdownDisplay(display_name="Network graph Results", field_id="analysis_result", required=False, value=tom,
                                          info="Network graph (chay_thu: vẽ bằng code)", show=True),
            metadata={"s3_urls": [tai["s3_url"]], "input_file_count": len(files), "output_file_count": 1, "chay_thu_do_thi": True,
                      "so_nut": len(nut), "so_canh": len(canh), "canh_bao": canh_bao, "sha16_html": hashlib.sha256(noi_dung).hexdigest()[:16],
                      "message_history": lich_su},
        )
        await node.emit_success(f"Đồ thị {len(nut)} nút, {len(canh)} cạnh")
        await node.emit_node_result(output)
        if getattr(node, "runtime", None):
            for port in node.output_ports:
                await node.runtime.emit_output(port.id, output)
        await node.emit_end()
        return output
    except Exception as e:
        capture_exception(e)
        msg = f"Network Graph (chay_thu vẽ bằng code) lỗi: {type(e).__name__}: {e}"
        if "output retries" in str(e):   # [D m4] pydantic-ai chỉ báo "Exceeded maximum output retries" ⇒ nói rõ trần
            msg += f" — nội dung AI trả sai khuôn hoặc vượt trần (≤ {TRAN_NUT} nút, ≤ {TRAN_CANH} cạnh, nhãn ≤ 200 ký tự); thu hẹp yêu cầu rồi chạy lại"
        msg = msg[:800]
        logger.error(msg, exc_info=True)
        await node.emit_error({"error": msg})
        await node.emit_end({"error": msg})
        return NodeOutput(data=None, node=node, error=msg, metadata={"chay_thu_do_thi": True, "canh_bao": canh_bao})
