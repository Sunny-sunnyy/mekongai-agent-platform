"""[ghi chú] Khối PowerPoint (pptx_chat_node) / Word (docx_chat_node) chạy bằng
CÔNG CỤ XUẤT FILE ĐẸP CỦA MÌNH thay cho E2B + Claude. Gắn ở: /srv/src/nodes/output/_file_dep_khoi.py (CHƯA gắn — chờ D kiểm).

Gốc Capitol [code, agentic-backend release-4.10.0]: khối dựng E2BSkillsChatService (powerpoint_chat/node.py:76, word_doc_chat/node.py:78)
⇒ Claude VIẾT CODE python-pptx / python-docx trong hộp E2B (có mạng mặc định), chat nhiều lượt chờ người, rồi tải file từ hộp lên S3 với khoá
`{s3_folder}/{uuid}{đuôi}` — KHÔNG có mã tổ chức (services/pydantic_ai_skills/e2b_chat_service.py:1333).
Bản thay: "AI soạn, code dựng" — gpt-6-luna chỉ trả NỘI DUNG có khuôn (pydantic) ⇒ make_pptx / make_docx (nodes/agent/xuat_file.py, bản đã
duyệt 11e7250b) dựng file ⇒ tải lên bucket outputs (MinIO trên máy) với khoá có mã tổ chức của LƯỢT ⇒ NodeFile như cũ. Không chạy code AI viết,
không hộp, không Claude.

MẤT so với Capitol (A chấp nhận cho v1 — ghi rõ trong đầu ra khối, không lặng lẽ):
  1. chat nhiều lượt / chờ người sửa (1 lượt);  2. mẫu trình bày (.pptx template) người dùng tải lên;
  3. NỘI DUNG file vào (chỉ dùng chữ của yêu cầu + khối trước nối vào ô);  4. AI tự trang trí tuỳ ý (khuôn cố định của mình).
Cờ: CHAY_THU_FILE_DEP_KHOI (mặc định BẬT; false / 0 / no / off ⇒ đúng đường Capitol gốc).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from io import BytesIO
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAU = Literal["than_chi", "dem_xanh", "rung_reu", "san_ho", "dat_nung", "dai_duong", "xanh_ngoc", "dau_kem", "anh_dao"]   # xuat_file.make_pptx
KIEU = Literal["gach_dau_dong", "hai_cot", "so_sanh", "so_lon", "dong_thoi_gian", "bang", "bia"]
Muc = Annotated[str, Field(max_length=600)]      # MAX_ITEM_CHARS (xuat_file.py:40)
Doan = Annotated[str, Field(max_length=5000)]    # MAX_DOAN_CHARS (xuat_file.py:43)
Cot = Annotated[str, Field(max_length=600)]
LOAI = {   # đuôi, content-type, thư mục S3 (giữ thư mục của Capitol: services/document_generation/{ppt,docx}_chat/config.py:11)
    "pptx": (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "documents/pptx_chat"),
    "docx": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "documents/docx_chat"),
}


# [gom minor 09/11 — D m2 mục 55 / 59] mã tổ chức đi vào khoá S3 ⇒ kiểm KHUÔN (chặn "/", "..", ký tự lạ — khoá không trèo sang thư mục khác)
_ORG = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _org_hop_le(orgid) -> bool:
    return isinstance(orgid, str) and bool(_ORG.fullmatch(orgid))


def bat() -> bool:
    return os.environ.get("CHAY_THU_FILE_DEP_KHOI", "true").strip().lower() not in ("false", "0", "no", "off")


# ---- khuôn nội dung AI phải trả (giới hạn = giới hạn của make_* — xuat_file.py:38-43 — để lỗi bị bắt ở lớp kiểm khuôn, AI được sửa lại) ----
class TrangPptx(BaseModel):
    tieu_de: str = Field(max_length=300, description="Tiêu đề trang")
    kieu: KIEU = Field("gach_dau_dong", description="Kiểu bố cục của trang")
    muc: list[Muc] = Field(min_length=1, max_length=20, description="Nội dung trang, mỗi mục ≤ 600 ký tự")


class BaiPptx(BaseModel):
    tieu_de: str = Field(max_length=300)
    phu_de: str | None = Field(None, max_length=600)
    chu_de_mau: MAU | None = None
    trang: list[TrangPptx] = Field(min_length=1, max_length=30)


class BaiDocx(BaseModel):
    tieu_de: str = Field(max_length=300)
    phu_de: str | None = Field(None, max_length=600)
    chu_de_mau: MAU | None = None
    doan: list[Doan] = Field(min_length=1, max_length=200, description="Các đoạn; '## ' = tên mục, '- ' = gạch đầu dòng, **đậm**")
    bang_cot: list[Cot] | None = Field(None, max_length=30, description="Tên cột của MỘT bảng (tuỳ chọn)")
    bang_dong: list[list[Cot]] | None = Field(None, max_length=1000, description="Các dòng của bảng, cùng số cột với bang_cot")


_CHUNG = (
    "Bạn soạn NỘI DUNG; hệ thống tự dựng file có thiết kế từ nội dung bạn trả (bạn không viết code, không dựng file).\n"
    "- Viết cùng ngôn ngữ với yêu cầu.\n"
    "- Chỉ dùng thông tin có trong yêu cầu / dữ liệu được đưa; KHÔNG bịa số liệu, tên, ngày, nguồn. Thiếu dữ liệu thì ghi rõ là thiếu.\n"
    "- KHÔNG viết đường dẫn (link) nào; không ghi lời cam kết về cách làm vào nội dung file.\n"
)
SYS = {
    "pptx": _CHUNG + (
        "- Trả bài trình chiếu: tiêu đề, (tuỳ chọn) phụ đề, các trang; mỗi trang có tiêu đề, kiểu bố cục và các mục.\n"
        "- Kiểu: gach_dau_dong (các ý); hai_cot / so_sanh (BẮT BUỘC một mục \"---\" ngăn hai bên, mục đầu mỗi bên là tên cột);\n"
        "  so_lon (1–6 mục \"con số | nhãn\", số đứng trước dấu |); dong_thoi_gian (mục \"mốc | mô tả\", mốc là thời điểm);\n"
        "  bang (mục đầu \"Cột A | Cột B\", các mục sau là dòng \"giá trị | giá trị\"); bia (trang chuyển phần / kết luận).\n"
        "- Đổi kiểu giữa các trang cho bài sinh động. Dấu \"|\" chỉ dùng cho so_lon / dong_thoi_gian / bang.\n"),
    "docx": _CHUNG + (
        "- Trả tài liệu Word: tiêu đề, (tuỳ chọn) phụ đề, các đoạn; đoạn bắt đầu \"## \" là tên mục, \"- \" là gạch đầu dòng (mỗi ý một đoạn).\n"
        "- Cần bảng thì đưa bang_cot + bang_dong (cùng số cột).\n"),
}


async def _soan(loai: str, he_thong: str, yeu_cau: str, orgid: str):
    """gpt-6-luna trả nội dung có khuôn. Cách gọi như vá 11 (sua_doi/services/custom_guardrail_check/…:181-197)."""
    from pydantic_ai import Agent

    from ai.agentic.models.openai import get_openai_model_for_agent_node
    from core.config.ai.openai import OpenAIModels
    from services.provider_keys import Provider, resolve_api_key_for_provider

    agent = Agent(
        model=get_openai_model_for_agent_node(OpenAIModels.GPT_6_LUNA, api_key=await resolve_api_key_for_provider(Provider.OPENAI, orgid)),
        system_prompt=he_thong,
        output_type=BaiPptx if loai == "pptx" else BaiDocx,
        retries=2,   # khuôn sai (quá dài, thiếu trường) ⇒ pydantic-ai báo lại AI sửa, tối đa 2 lần
    )
    r = await agent.run(yeu_cau, model_settings={"max_tokens": 4000})
    return r.output, json.loads(r.all_messages_json())


async def _dung(loai: str, bai) -> dict:
    """make_pptx / make_docx của mình (đã duyệt 11e7250b). Lỗi ⇒ ValueError có lý do (không lặng lẽ)."""
    from pathlib import Path

    from nodes.agent import xuat_file
    from nodes.agent.xuat_file import EXPORT_DIR, make_docx, make_pptx

    them = {"chu_de_mau": bai.chu_de_mau} if bai.chu_de_mau else {}
    if loai == "pptx":
        kq = await make_pptx(ten_file="pptx_chat", tieu_de=bai.tieu_de, slides=[[t.tieu_de, *t.muc] for t in bai.trang],
                             kieu_trang=[t.kieu for t in bai.trang], phu_de=bai.phu_de, **them)
    else:
        kq = await make_docx(ten_file="docx_chat", tieu_de=bai.tieu_de, doan=bai.doan, bang_cot=bai.bang_cot, bang_dong=bai.bang_dong,
                             phu_de=bai.phu_de, **them)
    if not kq.get("ok"):
        raise ValueError(f"Dựng file {loai} lỗi: {kq.get('error')}")
    # [D 68.D m1, gắn V5] không resolve() rồi mới đọc (kiểm một đằng đọc một nẻo): lấy đúng TÊN, buộc tên == kq["file"], đọc không theo
    # symlink bằng xuat_file._doc (O_NOFOLLOW — file đẹp bản 5).
    p = EXPORT_DIR / Path(kq["file"]).name
    if p.name != kq["file"]:   # tên file do make_* sinh (_safe_name) — có "/" hay ".." là lạ ⇒ từ chối
        raise ValueError("đường file xuất không hợp lệ")
    return {"kq": kq, "bytes": xuat_file._doc(p)}


async def _tai_len(loai: str, ten: str, data: bytes, orgid: str) -> dict:
    """Tải lên bucket outputs (MinIO trên máy). Khoá có MÃ TỔ CHỨC CỦA LƯỢT (D S6) — khác Capitol gốc (e2b_chat_service.py:1333)."""
    from core.settings import settings
    from services.aws.s3 import S3Service

    if not _org_hop_le(orgid):
        raise ValueError("mã tổ chức của lượt trống hoặc sai khuôn — không ghi file ra")
    bucket = settings.OUTPUTS_BUCKET_NAME
    if not bucket:
        raise ValueError("OUTPUTS_BUCKET_NAME trống — không ghi file ra")
    duoi, ct, thu_muc = LOAI[loai]
    key = f"{thu_muc}/{orgid}/{uuid4()}{duoi}"
    s3 = S3Service()
    await asyncio.to_thread(s3.upload_file, file=BytesIO(data), bucket_name=bucket, key=key, extras={"ContentType": ct})
    url = await asyncio.to_thread(s3.generate_presigned_url, s3_key=key, bucket_name=bucket, filename=ten, content_type=ct)
    return {"filename": ten, "s3_url": url, "s3_bucket": bucket, "s3_key": key, "s3_region": s3.region or "us-east-1", "content_type": ct}


async def chay_khoi(node, loai: str):
    """Nhánh thay cho execute() gốc của 2 khối. Trả NodeOutput, phát sự kiện như gốc (emit_success / node_result / output / end)."""
    from sentry_sdk import capture_exception

    from nodes.grounding import apply_grounding
    from workflow.node.base.node import NodeOutput
    from workflow.node.base.param.custom import PowerPointDocumentDisplay, WordDocumentDisplay

    ten_khoi = "PowerPoint" if loai == "pptx" else "Word"
    canh_bao: list[str] = []
    try:
        node._validated_params = await node._get_validated_params()
        p = node._validated_params
        yeu_cau = node._get_user_prompt()
        if not yeu_cau.strip():
            raise ValueError("khối không có yêu cầu (ô request trống)")
        files = await node._extract_input_files()
        if files:   # MẤT 3 — ghi rõ, A yêu cầu
            # [D m3 mục 55] tên file do người tải lên đặt ⇒ làm sạch trước khi vào cảnh báo (hiện trong giao diện dạng Markdown)
            ten = ", ".join(re.sub(r"[^\w.\- ]", "_", str(f.name or ""))[:80] for f in files)[:300]
            canh_bao.append(f"Khối nhận {len(files)} file ({ten}) nhưng bản thay chay_thu CHƯA đọc "
                            "nội dung file — chỉ dùng chữ của yêu cầu.")
        if loai == "pptx" and p.get("template_file"):   # MẤT 2
            canh_bao.append("Có mẫu trình bày (template_file) nhưng bản thay chay_thu CHƯA dùng mẫu — dựng theo khuôn cố định.")
        he_thong = SYS[loai]
        if loai == "pptx" and isinstance(p.get("system_prompt"), str) and p["system_prompt"].strip():
            he_thong += f"\n## Dặn thêm của người dùng\n{p['system_prompt'].strip()}"
        he_thong = apply_grounding(he_thong, p.get("ground_with_provided_info", False))
        for c in canh_bao:
            await node.emit_progress(dynamic_status=c)
        # kiểm chỗ ghi TRƯỚC khi gọi model (thử K4 09/11: thiếu mã tổ chức mà vẫn gọi AI rồi mới báo lỗi ⇒ tốn một lượt vô ích)
        from core.settings import settings as _st

        if not node._orgid:
            raise ValueError("không có mã tổ chức của lượt — không ghi file ra")
        if not _org_hop_le(node._orgid):
            raise ValueError("mã tổ chức của lượt sai khuôn — không ghi file ra")
        if not _st.OUTPUTS_BUCKET_NAME:
            raise ValueError("OUTPUTS_BUCKET_NAME trống — không ghi file ra")
        await node.emit_progress(dynamic_status=f"Đang soạn nội dung {ten_khoi} (gpt-6-luna) …")
        bai, lich_su = await _soan(loai, he_thong, yeu_cau, node._orgid)
        dung = await _dung(loai, bai)
        kq = dung["kq"]
        canh_bao += [f"xuat_file: {x}" for x in (kq.get("canh_bao") or [])]
        tai = await _tai_len(loai, kq["file"], dung["bytes"], node._orgid)
        node_files = node._create_output_node_files([tai])
        url = tai["s3_url"]
        Hien = PowerPointDocumentDisplay if loai == "pptx" else WordDocumentDisplay
        output = NodeOutput(
            files=node_files, data=url, node=node,
            display_model=Hien(display_name=f"{ten_khoi} Document", field_id=f"{loai}_result", required=False, value=url, show=True,
                               info=f"{ten_khoi} (chay_thu: công cụ xuất file của mình)" + (f" — CẢNH BÁO: {' | '.join(canh_bao)}" if canh_bao else "")),
            metadata={"agent_name": p.get("name", f"{ten_khoi} Agent"), "s3_urls": [url], "chat_rounds": 1,
                      "chay_thu_file_dep": True, "canh_bao": canh_bao, "file_vao_chua_doc": [f.name for f in files],
                      "xuat_file": {k: kq.get(k) for k in ("file", "so_trang", "so_doan", "sha256", "chu_de_mau")},
                      "message_history": lich_su},   # cùng chỗ sổ tiền chay_s5.tien_luot đọc token
        )
        await node.emit_success(url)
        await node.emit_node_result(output)
        if getattr(node, "runtime", None):
            for port in node.output_ports:
                await node.runtime.emit_output(port.id, output)
        await node.emit_end()
        return output
    except Exception as e:
        capture_exception(e)
        msg = f"{ten_khoi} (chay_thu file đẹp) lỗi: {type(e).__name__}: {e}"[:800]
        logger.error(msg, exc_info=True)
        await node.emit_error({"error": msg})
        await node.emit_end({"error": msg})
        return NodeOutput(data=None, node=node, error=msg, metadata={"chay_thu_file_dep": True, "canh_bao": canh_bao})
