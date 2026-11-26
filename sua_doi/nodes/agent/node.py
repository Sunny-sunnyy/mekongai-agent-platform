"""The generic Agent node.

The reference member of the ``AgentBaseNode`` hierarchy (ENG-4437): the
base class defaults ARE this node's semantics, so the subclass only
declares identity, its clarification opt-in, and its params/ports.

Usage example with payload at : app/nodes/agent/tests/usage_example.py
"""

import logging

from nodes.agent.params import PARAMS
from nodes.agent.ports.input import INPUT_PORTS
from nodes.agent.ports.output import OUTPUT_PORTS
from workflow.node.base.agent import AgentBaseNode
from workflow.node.registry import register

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _cau_hoi_cua_khoi(vp: dict) -> str:
    """[lam_lai] Câu hỏi cho lần tự kiểm = ô user_prompt của chính khối (workflow KPI: nối từ Ô nhập). Chỉ ảnh hưởng việc CÓ làm lại hay
    không — Rào chắn lấy câu hỏi từ ô cau_hoi riêng của nó (kiem_payload chỉ cho nối từ Ô nhập)."""
    v = (vp or {}).get("user_prompt")
    if isinstance(v, list):
        v = "\n".join(str(x) for x in v)
    return v.strip() if isinstance(v, str) else ""


def _chu_dau_vao(v) -> str:
    """[chan_loi_mem] Cùng cách lớp gốc đổi user_prompt thành chữ (workflow/node/base/agent/node.py:798–813) — để kiểm đúng chữ mô hình nhận
    (gốc: dict / kiểu khác ⇒ str(v), nên None ⇒ "None")."""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(f"{i + 1}. {x}" for i, x in enumerate(v))
    if isinstance(v, dict):
        return "\n".join(f"{k}: {x}" for k, x in v.items())
    return str(v)


def _loi_nhac_lam_lai(ly_do: list[str]) -> str:
    """[lam_lai, A điều kiện c] Câu cố định + ĐÚNG lý do dịch vụ kiểm trả (đã cắt ở _ly_do_kiem_code) — không thêm dữ liệu khác."""
    dong = "\n".join(f"- {x}" for x in ly_do[:10])
    return ("Câu trả lời vừa rồi bị hệ thống kiểm số chặn. Lý do hệ thống kiểm trả:\n" + dong +
            "\nHãy trả lời lại câu hỏi ban đầu: mọi con số phải lấy đúng từ kết quả công cụ của lượt này (gọi công cụ nếu cần); "
            "không nhắc lại số sai.")


@register
class AgentNode(AgentBaseNode):
    version = "0.2.0"

    display_name = "Agent"
    description = (
        "A configurable AI agent that can reason, make decisions, or perform tasks"
    )
    icon = "BotMessageSquareIcon"

    show = True
    beta = False

    node_id = "agent_node"
    node_type = "agent"
    node_sub_type = "base_agent"

    # de-scope: opt the agent node class into clarifying questions.
    # It's the only node type enabled for now — no competing in-place pause
    # mechanism, so it's the cleanest place to prove the pause/re-dispatch
    # flow. See ``BaseNode._supports_clarifications`` for full rationale.
    _supports_clarifications: bool = True

    input_ports = INPUT_PORTS
    output_ports = OUTPUT_PORTS

    params = PARAMS

    def _compose_agent_tools(self) -> list:
        # [VÁ 8 chay_thu 04/11] hook có sẵn của Capitol (workflow/node/base/agent/node.py:387) — thêm công cụ gọi thẳng, không qua MCP
        from nodes.agent.native_tools import build_native_tools

        # [VÁ B2b chay_thu 09/11, C] mã file do HỆ THỐNG điền từ file đầu vào (hook dưới) — thắng ô tool_ma_file tĩnh. Ghi vào bản sao params,
        # không vào self._validated_params (compile có thể đọc lại params từ cấu hình ⇒ ghi đè mất).
        params = dict(self._validated_params or {})
        ma_tai = getattr(self, "_ma_file_tai", None)
        if ma_tai:
            params["tool_ma_file"] = ma_tai
        elif "tai_" in str(params.get("tool_ma_file") or "").lower():
            # [ghi chú] file tai_* là file NGƯỜI DÙNG TẢI LÊN (của một tổ chức), người chạy hộp không kiểm tổ chức
            # (sandbox/nguoi_chay_hop.py:109-149) ⇒ chỉ HỆ THỐNG được đưa tên tai_* (qua _ma_file_tai, đã kiểm khoá thuộc tổ chức của lượt).
            # Ô tĩnh chứa "tai_" (bất kể hoa / thường / tiền tố đường dẫn) ⇒ bỏ ⇒ run_python trả chua_co_file. Ô tĩnh = file mẫu du_lieu/excel/ vẫn dùng.
            logger.warning("[NATIVE_TOOL] bỏ ô tool_ma_file tĩnh dạng tai_* (chỉ hệ thống được gắn file tải lên)")
            params["tool_ma_file"] = ""
        from nodes.agent.native_tools import _on, kiem_kpi_khong_ra_ngoai

        kiem_kpi_khong_ra_ngoai(params, co_khoi_cong_cu=bool(getattr(self, "handle_nodes", None)))   # [ghi chú]
        _e2b_fn = getattr(self, "_e2b_dung", None)
        _e2b_bat = bool(_e2b_fn is not None and _e2b_fn())
        _e2b_code = _e2b_bat and (_on(params.get("tool_run_python")) or getattr(self, "_sandbox", None) is not None)
        # [ghi chú] công cụ KPI: mã lượt = WorkflowRun.id (temporal/utils/node_factory.py:118) + tổ chức — thuộc tính hệ thống của khối
        return super()._compose_agent_tools() + build_native_tools(params, run_id=str(getattr(self, "_run_id", "") or "").strip(),
                                                                   orgid=str(getattr(self, "_orgid", "") or "").strip(),
                                                                   e2b_run_code=_e2b_code)

    async def _run(self, agent):
        """[chan_loi_mem B 16/11/2026 — đã giao, đã soát giấy ĐẠT; bản sao va_chan_loi_mem/, gắn thứ Tư] Bọc NGOÀI vòng KPI bên dưới:
        TRƯỚC khi gọi mô hình: đầu vào rỗng / "None" / {biến} / [Insert…] ⇒ chế độ "chan": dừng, 0 token; SAU khi xong (kể cả sau vòng làm lại
        KPI — D minor 3 / S6): mọi công cụ lỗi / mọi lần chạy code lỗi / chỉ xin lỗi ⇒ "chan": khối báo lỗi (không thử lại — chu_loi).
        Chế độ "do" (mặc định): chỉ ghi nhãn vào metadata `chan_loi_mem`, câu trả lời giữ nguyên. "tat": bỏ hẳn."""
        from nodes.agent import kiem_loi_mem as KL
        from nodes.agent.native_tools import _on

        mode, canh_bao = KL.che_do()
        nhan = {"che_do": mode, "truoc": [], "sau": []}
        if canh_bao:
            nhan["canh_bao"] = canh_bao
            logger.warning("agent CHAN_LOI_MEM %s", canh_bao)
        self._chan_loi_mem = None if mode == "tat" else nhan   # đặt lại MỖI lần chạy
        if mode == "tat":
            return await self._run_kpi(agent)
        vp = self._validated_params or {}
        if not getattr(self, "_pending_feedback_prompt", None):   # lượt làm lại do code mình viết chữ ⇒ không xét
            khuon, co_day = KL.o_user_prompt(getattr(self, "node_config", None))   # [vòng 2] so tin gửi với chữ gõ sẵn
            try:   # [E minor 16/11] lỗi trong chính phép kiểm ⇒ không làm hỏng lượt (luật mềm), ghi log
                nhan["truoc"] = KL.kiem_dau_vao(_chu_dau_vao(vp.get("user_prompt")), co_day_vao=co_day, khuon=khuon)
            except Exception as e:  # noqa: BLE001
                nhan["truoc"], nhan["loi_kiem"] = [], type(e).__name__
                logger.warning("agent CHAN_LOI_MEM loi_kiem truoc=%s", type(e).__name__)
            if nhan["truoc"]:
                logger.warning("agent CHAN_LOI_MEM truoc=%s che_do=%s", ",".join(nhan["truoc"]), mode)
                if mode == "chan":
                    raise ValueError(KL.chu_loi(nhan["truoc"], truoc=True))
        ket = await self._run_kpi(agent)
        try:   # [E minor 16/11]
            nhan["sau"] = KL.kiem_dau_ra(ket, self._final_message_history, co_kpi=_on(vp.get("tool_kpi")))
        except Exception as e:  # noqa: BLE001
            nhan["sau"], nhan["loi_kiem"] = [], type(e).__name__
            logger.warning("agent CHAN_LOI_MEM loi_kiem sau=%s", type(e).__name__)
        if nhan["sau"]:
            logger.warning("agent CHAN_LOI_MEM sau=%s che_do=%s", ",".join(nhan["sau"]), mode)
            if mode == "chan":
                raise ValueError(KL.chu_loi(nhan["sau"], truoc=False))
        # [ghi chú] có file đã vào hộp
        # (_ma_file_tai — hook _chay_thu_nhan_file_vao_hop) mà không lần run_python nào mở file ⇒ nhãn. Lỗi khi xét ⇒ bỏ qua (không làm hỏng lượt).
        try:
            nhan["doc_file"] = KL.kiem_doc_file("agent_node", bool(getattr(self, "_ma_file_tai", None)), self._final_message_history)
        except Exception:  # noqa: BLE001
            nhan["doc_file"] = []
        if nhan["doc_file"]:
            logger.warning("agent CHAN_LOI_MEM doc_file=%s che_do=%s", ",".join(nhan["doc_file"]), mode)
        return ket

    async def _run_kpi(self, agent):
        """[lam_lai C 10/11/2026 — đã chốt P1; M3 câu 7 / 9] Hook Capitol CHO PHÉP ghi đè (workflow/node/base/agent/node.py:145-159, mặc định
        :508-521). Chỉ khi bật tool_kpi: chạy như gốc ⇒ TỰ KIỂM câu bằng đúng dịch vụ của Rào chắn ⇒ trượt ⇒ làm lại ĐÚNG 1 lần bằng
        `_run_feedback_turn` (hàm @final có sẵn :915-937 — chạy tiếp CÙNG hội thoại, giữ công cụ + lịch sử) kèm lý do dịch vụ trả.
        Đây CHỈ là gợi ý làm lại, KHÔNG phải cổng: câu (lần 1 hay lần 2) vẫn sang Rào chắn, Rào kiểm lại từ đầu — lần 2 sai ⇒ Rào chặn ⇒ dừng.
        Đóng khi lỗi (A điều kiện b): dịch vụ kiểm lỗi / hết giờ, thiếu lịch sử hội thoại, lần làm lại lỗi ⇒ KHÔNG làm lại, trả câu gốc."""
        from nodes.agent.native_tools import _on

        ket = await super()._run(agent)
        if not _on((self._validated_params or {}).get("tool_kpi")):
            if _on((self._validated_params or {}).get("tool_sql_query")):   # [va_kiem_so_sql C 12/11]
                return await self._sql_tu_kiem_lam_lai(agent, ket)
            return ket                                     # khối Agent khác: đúng hành vi Capitol, không thêm gì vào metadata
        ghi = {"tu_kiem": None, "so_lan_lam_lai": 0, "ly_do": []}
        self._kpi_lam_lai = ghi                            # đặt lại MỖI lần chạy (một instance có thể chạy lại khi Temporal thử lại)
        if agent is None or not isinstance(ket, str) or not ket.strip():
            ghi["tu_kiem"] = "bo_qua"                      # câu rỗng / không phải chữ ⇒ để Rào chắn chặn (Đ-1), không làm lại
            return ket
        from nodes.agent import kiem_so_ai as KS           # [va_ai_chan_so 20/11] công tắc đọc MỖI lượt; file sai khuôn ⇒ ném ⇒ khối dừng với lỗi rõ (không tự chọn)

        cd = KS.che_do()
        if cd["quyet_dinh"] == "ai":
            return await self._kpi_ai_cham_va_lam_lai(agent, ket, KS)
        from nodes.agent.native_tools import kpi_data_version
        from nodes.governance.guardrails.node import tu_kiem_kpi

        try:
            ly_do = await tu_kiem_kpi(ket, _cau_hoi_cua_khoi(self._validated_params),
                                      str(getattr(self, "_run_id", "") or "").strip(), str(getattr(self, "_orgid", "") or "").strip(),
                                      data_version=kpi_data_version(self._validated_params))   # [va_data_version] cùng bản dữ liệu công cụ đã dùng
        except Exception as e:  # noqa: BLE001 — ĐÓNG KHI LỖI: không làm lại, Rào chắn vẫn kiểm câu gốc
            ghi["tu_kiem"] = f"loi: {type(e).__name__}"
            logger.warning("agent KPI_TU_KIEM loi=%s — không làm lại", type(e).__name__)
            return ket
        if not ly_do:
            ghi["tu_kiem"] = "dat"
            return ket
        ghi.update(tu_kiem="truot", ly_do=ly_do)
        if not self._final_message_history:                # không có lịch sử ⇒ lượt làm lại không thấy câu hỏi gốc ⇒ không làm
            ghi["lam_lai"] = "bo_qua: không có lịch sử hội thoại"
            return ket
        ghi["so_lan_lam_lai"] = 1                          # đếm TRƯỚC khi gọi: lần làm lại lỗi giữa chừng vẫn tính là đã dùng lượt
        try:
            moi = await self._run_feedback_turn(agent, _loi_nhac_lam_lai(ly_do))
        except Exception as e:  # noqa: BLE001 — lần làm lại hỏng ⇒ trả câu gốc (Rào sẽ chặn vì câu gốc đã trượt)
            ghi["lam_lai"] = f"loi: {type(e).__name__}"
            logger.warning("agent KPI_LAM_LAI loi=%s — trả câu gốc", type(e).__name__)
            return ket
        ghi["lam_lai"] = "xong"
        # một dòng log: không nội dung khách, chỉ trạng thái + số lý do
        logger.warning("agent KPI_LAM_LAI so_lan=1 so_ly_do=%d", len(ly_do))
        return moi if isinstance(moi, str) else ket

    async def _kpi_ai_cham_va_lam_lai(self, agent, ket: str, KS):
        """[ghi chú] Chế độ `ai` của công tắc: AI CHẤM SỐ là lớp quyết, đặt Ở ĐÂY vì chỉ khối này đang
        giữ NGUYÊN kết quả công cụ của lượt (lịch sử lời gọi trong bộ nhớ, chưa qua cắt). Trình tự:
          1. lớp code tự kiểm như cũ — CHỈ GHI ý kiến (không dùng để làm lại, không chặn);
          2. AI chấm câu trả lời; không đạt (và chấm được) ⇒ Trợ lý viết lại ĐÚNG một lần kèm các chỗ bị bắt ⇒ AI chấm lại câu mới;
          3. ghi PHIẾU kết quả cho câu CUỐI (khoá: tổ chức + phiên + mã băm câu) — khối Rào chỉ cho qua khi có phiếu ĐẠT của đúng câu nó cầm;
          4. mỗi lần chấm một dòng nhật ký hai ý kiến.
        ĐÓNG khi lỗi: AI chấm không chạy được ⇒ phiếu "không chấm được" (Rào chặn, lời báo rõ); không ghi được phiếu ⇒ Rào không thấy phiếu ⇒ chặn."""
        from nodes.agent.native_tools import kpi_data_version
        from nodes.governance.guardrails.node import tu_kiem_kpi
        from workflow.node.base.agent.streaming import serialize_message_history

        ghi = self._kpi_lam_lai
        ghi["che_do"] = "ai"
        run_id, org = str(getattr(self, "_run_id", "") or "").strip(), str(getattr(self, "_orgid", "") or "").strip()
        phien, ma_nut = str(getattr(self, "_session_id", "") or "").strip(), str(getattr(self, "node_id", "") or "")
        cau_hoi = _cau_hoi_cua_khoi(self._validated_params)
        dv = kpi_data_version(self._validated_params)

        async def y_kien_code(cau: str):   # lớp code: chạy kèm, chỉ ghi
            try:
                ly_do = await tu_kiem_kpi(cau, cau_hoi, run_id, org, data_version=dv)
                return {"dat": not ly_do, "ly_do": [str(x)[:120] for x in ly_do[:10]]}
            except Exception as e:  # noqa: BLE001
                return {"dat": None, "loi": type(e).__name__}

        async def mot_lan(cau: str, lan: int):
            code = await y_kien_code(cau)
            cong_cu = KS.cong_cu_tu_lich_su(serialize_message_history(self._final_message_history) if self._final_message_history else [])
            kq = await KS.cham(cau_hoi, cau, cong_cu, orgid=org or None, data_version=dv)
            k = kq.get("ket_qua") or {}
            logger.info("[KIEM_SO_AI] session=%s node=%s model=%s requests=%s input=%s output=%s dat=%s", phien, ma_nut, kq.get("mo_hinh"), 1 + int(kq.get("goi_lai") or 0),
                        int(kq.get("token_vao") or 0), int(kq.get("token_ra") or 0), int(bool(kq.get("dat"))))
            dong = {"noi": "tro_ly", "phien": phien, "run_id": run_id, "ma_nut": ma_nut, "lan": lan, "sha_cau": KS.bam_cau(cau), "so_cong_cu": len(cong_cu),
                    "ai": {"dat": bool(kq.get("dat")), "khong_chac": k.get("khong_chac"), "ly_do": [x.get("ly_do") for x in (k.get("so_sai") or [])][:20], "loi": kq.get("loi"),
                           "goi_lai": kq.get("goi_lai"), "giay": kq.get("giay"), "token_vao": kq.get("token_vao"), "token_ra": kq.get("token_ra"), "mo_hinh": kq.get("mo_hinh")},
                    "code": code}
            if code.get("dat") is not None and bool(kq.get("dat")) != bool(code.get("dat")):   # hai ý kiến KHÁC nhau ⇒ lưu câu hỏi + câu trả lời để người xem lại (chỉ trên máy)
                dong["cau_hoi"], dong["cau_tra_loi"] = cau_hoi[:4000], cau[:20000]
            KS.ghi_nhat_ky(dong)
            return kq, code

        kq, code = await mot_lan(ket, 1)
        ghi["tu_kiem"] = "dat" if code.get("dat") else ("truot" if code.get("dat") is False else f"loi: {code.get('loi')}")   # ý kiến của CODE (chỉ ghi)
        ghi["ai_cham"] = [{"lan": 1, "dat": bool(kq.get("dat")), "loi": kq.get("loi"), "giay": kq.get("giay")}]
        cuoi, lan = ket, 1
        if not kq.get("dat") and kq.get("ket_qua") and self._final_message_history:   # chấm ĐƯỢC và không đạt ⇒ viết lại một lần (không chấm được thì không biết sửa gì)
            ghi["so_lan_lam_lai"] = 1                      # đếm TRƯỚC khi gọi (như đường cũ)
            try:
                moi = await self._run_feedback_turn(agent, KS.loi_nhac_viet_lai(kq))
                ghi["lam_lai"] = "xong"
            except Exception as e:  # noqa: BLE001 — lần viết lại hỏng ⇒ giữ câu gốc (đã có kết luận không đạt ⇒ Rào chặn)
                moi = None
                ghi["lam_lai"] = f"loi: {type(e).__name__}"
                logger.warning("agent KPI_LAM_LAI (ai) loi=%s — giữ câu gốc", type(e).__name__)
            if isinstance(moi, str) and moi.strip():
                cuoi, lan = moi, 2
                kq, code = await mot_lan(moi, 2)
                ghi["ai_cham"].append({"lan": 2, "dat": bool(kq.get("dat")), "loi": kq.get("loi"), "giay": kq.get("giay")})
        try:
            await KS.ghi_phieu(org, phien, cuoi, KS.dung_phieu(kq, cuoi, "ai", ma_nut, lan))
            ghi["phieu"] = "da_ghi"
        except Exception as e:  # noqa: BLE001 — không có phiếu ⇒ khối Rào chặn (đóng); ghi rõ để người vận hành thấy
            ghi["phieu"] = f"loi: {type(e).__name__}"
            logger.warning("agent KIEM_SO_AI không ghi được phiếu (%s) — khối Rào sẽ chặn câu này", type(e).__name__)
        logger.warning("agent KIEM_SO_AI lan=%d dat=%s lam_lai=%s", lan, bool(kq.get("dat")), ghi.get("so_lan_lam_lai"))
        return cuoi

    async def _sql_tu_kiem_lam_lai(self, agent, ket):
        """[va_kiem_so_sql C 12/11/2026 — đã giao "AI làm lại 1 lần như bên KPI nếu rẻ"] Khối bật tool_sql_query (không bật tool_kpi): TỰ SO số trong
        câu trả lời với sổ kết quả SQL của lượt (cùng hàm khối Rào chắn chế độ "sql" dùng); trượt ⇒ làm lại ĐÚNG 1 lần (cùng hội thoại) kèm lý do.
        CHỈ là gợi ý làm lại, KHÔNG phải cổng: câu nào đi ra cũng bị Rào chắn kiểm lại từ đầu. Đóng khi lỗi: không đọc được sổ / thiếu lịch sử /
        lần làm lại lỗi ⇒ KHÔNG làm lại, trả câu gốc. Tốn thêm tối đa 1 lượt AI, chỉ khi câu đầu trượt."""
        ghi = {"tu_kiem": None, "so_lan_lam_lai": 0, "ly_do": []}
        self._kpi_lam_lai = ghi                            # dùng chung chỗ ghi metadata `kpi_lam_lai` (bộ chấm đọc cùng một khoá)
        if agent is None or not isinstance(ket, str) or not ket.strip():
            ghi["tu_kiem"] = "bo_qua"
            return ket
        try:
            from nodes.agent import nho_sql_luot

            ban_ghi = await nho_sql_luot.doc(str(getattr(self, "_orgid", "") or "").strip(), str(getattr(self, "_run_id", "") or "").strip())
            ly_do = nho_sql_luot.ly_do_lam_lai(nho_sql_luot.kiem(ket, ban_ghi, _cau_hoi_cua_khoi(self._validated_params)))   # so số + luật 0 dòng
        except Exception as e:  # noqa: BLE001
            ghi["tu_kiem"] = f"loi: {type(e).__name__}"
            logger.warning("agent SQL_TU_KIEM loi=%s — không làm lại", type(e).__name__)
            return ket
        if not ly_do:
            ghi["tu_kiem"] = "dat"
            return ket
        ghi.update(tu_kiem="truot", ly_do=ly_do)
        if not self._final_message_history:
            ghi["lam_lai"] = "bo_qua: không có lịch sử hội thoại"
            return ket
        ghi["so_lan_lam_lai"] = 1
        try:
            moi = await self._run_feedback_turn(agent, (
                "Câu trả lời vừa rồi bị hệ thống kiểm chặn: " + "; ".join(ly_do)[:1200] +
                "\nHãy trả lời lại câu hỏi ban đầu. Mọi con số trong câu trả lời phải là ĐÚNG một ô trong kết quả công cụ sql_query của lượt này: "
                "cần tổng, hiệu, tỉ lệ hay làm tròn thì viết thêm câu SQL để hệ thống tính rồi chép kết quả, không tự nhẩm; không nhắc lại số sai."))
        except Exception as e:  # noqa: BLE001
            ghi["lam_lai"] = f"loi: {type(e).__name__}"
            logger.warning("agent SQL_LAM_LAI loi=%s — trả câu gốc", type(e).__name__)
            return ket
        ghi["lam_lai"] = "xong"
        logger.warning("agent SQL_LAM_LAI so_lan=1 so_ly_do=%d", len(ly_do))
        return moi if isinstance(moi, str) else ket

    def _output_metadata(self, *, result, output_node_files, input_file_names) -> dict:
        """[lam_lai] Hook cho phép ghi đè (:546). Thêm `kpi_lam_lai` (tự kiểm, số lần làm lại, lý do) vào metadata ⇒ PG
        node_result.output_data->'metadata'->'kpi_lam_lai' cho bộ chấm. KHÔNG đổi `data` (Rào chắn nhận chữ ở ô guardrail_query)."""
        md = super()._output_metadata(result=result, output_node_files=output_node_files, input_file_names=input_file_names)
        ghi = getattr(self, "_kpi_lam_lai", None)
        if ghi is not None:
            md = dict(md, kpi_lam_lai=ghi)
        nhan = getattr(self, "_chan_loi_mem", None)   # [chan_loi_mem B 16/11] chỉ metadata — không vào `data` / cổng ra (D S7)
        if nhan is not None:
            md = dict(md, chan_loi_mem=nhan)
        return md

    async def _chay_thu_nhan_file_vao_hop(self, input_files: list) -> bool:
        """[VÁ B2b chay_thu 09/11, C] Gọi từ workflow/node/base/agent/node.py trước chỗ R12 cấm E2B.
        Chỉ nhận khi khối bật run_python VÀ nơi chạy không đặt E2B; khác ⇒ False."""
        from nodes.agent.native_tools import _on, nhan_file_vao_hop

        _e2b_fn = getattr(self, "_e2b_dung", None)
        if _e2b_fn is not None and _e2b_fn():
            return False
        if not _on((self._validated_params or {}).get("tool_run_python")):
            return False
        self._ma_file_tai = await nhan_file_vao_hop(input_files, str(getattr(self, "_orgid", "") or ""))
        return True

    def _chay_thu_chuan_bi_file_e2b(self) -> None:
        """[ghi chú] Khi agent_node bật run_python và đi E2B: nạp sẵn file mẫu tĩnh ở /srv/du_lieu/excel/ (nếu có, không phải tai_*)
        qua đúng rào _e2b_kiem_noi_dung TRƯỚC khi tạo máy ảo E2B."""
        import os
        from pathlib import Path
        from nodes.agent.native_tools import _on
        from workflow.node.base.agent import sandbox_runtime as _sr

        self._ct_ma_file_e2b = None
        params = self._validated_params or {}
        if not _on(params.get("tool_run_python")):
            return
        ma = str(params.get("tool_ma_file") or "").strip()
        if not ma or "tai_" in ma.lower():
            return
        if "/" in ma or "\\" in ma or ma in (".", "..") or ".." in ma:
            raise ValueError("E2B (chay_thu): tên file mẫu Excel không hợp lệ")
        goc = Path(os.environ.get("CHAY_THU_DU_LIEU_EXCEL", "/srv/du_lieu/excel")).resolve()
        duong = (goc / ma).resolve()
        if duong.parent != goc or not duong.is_file():
            raise ValueError(f"E2B (chay_thu): không thấy file mẫu '{ma[:80]}'")
        data = duong.read_bytes()
        if len(data) > _sr._E2B_TRAN_FILE_NGUOI_DUNG:
            raise ValueError(f"E2B (chay_thu): file mẫu '{ma[:80]}' vượt trần {_sr._E2B_TRAN_FILE_NGUOI_DUNG} byte")
        _sr._e2b_kiem_noi_dung(ma, data)
        self._ct_ma_file_e2b = (ma, data)
