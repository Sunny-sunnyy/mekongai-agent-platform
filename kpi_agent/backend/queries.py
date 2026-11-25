"""Truy vấn CHỈ ĐỌC của backend (bậc 1, `docs/SPEC.md` mục 4) — trước là 4 công cụ G4a ở `agent/tools.py`, dời sang đây 03/11 (P1)
để kiểm tham số nằm ở backend cho MỌI bên gọi (agent, trang React, gọi thẳng HTTP); `backend/main.py` bọc thành endpoint.

Chỉ gọi lõi tính sẵn có (khối 1–6) — không tự tính số. Mỗi kết quả là dict JSON được, gồm:
- số do code tính (tiền, %, thiếu bao nhiêu);
- `data_flags`: giới hạn dữ liệu do CODE điền (số chụp giữa tháng, 3 luật chưa kiểm được, giả định chờ sếp) — lời dặn bắt agent nói ra;
- `params_used`: tham số đã hiểu (kể cả đã chuẩn hoá, vd "pc_bt" ⇒ "PC_BT") — cả khi bị từ chối (chữ người gửi, cắt ≤ 40 ký tự).
Chỉ dùng mã che NV01–NV14 (`data/ma_che_ten.json`): tên / mã thật không bao giờ nằm trong kết quả công cụ (test kiểm).
Tham số sai (mã lạ, `../`, kỳ chưa có dữ liệu) ⇒ trả {"refused": lời từ chối, "params_used": …} — không ném lỗi, không trả số nào.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path

from kpi_agent.core.compute_bonus import floor_pct, kpi_tiers, round_2dp
from kpi_agent.core.compute_kpi import working_days
from kpi_agent.core.pending_rules import pending_rule_status
from kpi_agent.core.rules import KPI_ORDER
from kpi_agent.workflow import runner

EMPLOYEE_ID_PATTERN = re.compile(r"NV\d{2}")
# Bậc 4b (cuối file): nhãn trạng thái cố định — đúng chữ sản phẩm muốn mô hình nói (A: "chưa phải số chốt", "chưa duyệt")
STATUS = "tam_tinh"
STATUS_LABEL = "tạm tính — chưa phải số chốt, chưa duyệt"


def cong_van_section(ctx, kpi: str) -> dict:
    """Ô `cong_van_section` của công cụ: số mục công văn của KPI theo luật kỳ (`cong_van_sections` — bậc 3a, trước là hằng tháng 10 ở đây).
    Luật kỳ không ghi ⇒ KHÔNG có ô (lời dặn: chỉ nêu mục khi kết quả công cụ có ô này — mô hình không tự nhớ số mục)."""
    section = ctx.rules.get("cong_van_sections", {}).get(kpi)
    return {"cong_van_section": section} if section else {}


@dataclass
class KpiContext:
    """Mọi thứ công cụ cần, tính MỘT lần lúc mở phiên chat (cả quy trình ~1,6 s)."""
    period: str
    cutoff: date
    rules: dict
    results: dict          # mã thật ⇒ {kpi: KpiResult}
    bonuses: dict          # mã thật ⇒ employee_bonus(...)
    regions: dict          # mã thật ⇒ vùng (cột Region)
    to_real: dict          # NV01 ⇒ mã thật
    data_flags: list[str]
    days_total: int
    days_left: int
    snapshot_at: datetime | None = None   # lúc pivot Tracking làm mới lần cuối — trang hiện "số chụp …" (SPEC mục 6)
    rule_version: str = ""                # "<file luật>@<sha256 8 ký tự>" của file luật đang nạp (tính năng 4)
    # Bậc 2a: trạng thái của bản tính đang dùng — backend đặt từ bảng lưu (`store.py`) lúc bật và khi giám sát bấm "Đã soát".
    # `status_label` vào kết quả công cụ (mô hình đọc) — KHÔNG có tên người soát; `status_label_full` (có tên) chỉ cho trang qua /api/meta
    status: str = STATUS
    status_label: str = STATUS_LABEL
    status_label_full: str = STATUS_LABEL
    # Bước 1 S3: lượt chạy workflow đã tạo ra các số trên (bước, dấu vân tay đầu vào) — backend ghi vào bảng workflow_run
    workflow_result: object = None
    # Nhóm A — A6 (09/11): MỌI cờ của khối Kiểm dữ liệu (cả mức "info"), đã che mã — `data_flags` ở trên chỉ giữ cờ ngắn (trang); kết quả công cụ dùng `data_flags_for_model` dưới
    data_issues: list = field(default_factory=list)
    # mức tra cứu vòng 3 ( hướng (a) 09/11): cờ gắn vào MỌI kết quả công cụ = `data_flags` BỎ cờ của riêng người trong đội
    # (K3 vòng 2: câu về một người, mô hình nhận cờ của NV14 — chữ không nêu người — thành cờ của người được hỏi). Cờ đó chỉ còn ở
    # get_data_issues (A6, có mã NV). `data_flags` đủ vẫn cho trang qua /api/meta (giám sát cần thấy mọi cờ).
    data_flags_for_model: list = field(default_factory=list)
    # Bậc 1 nạp chung (17/11): kết quả cổng đối soát RAW (`load_raw.reconcile`: dòng vào / tính / loại theo lý do + tiền); đường sổ Tracking ⇒ None
    raw_reconciliation: dict | None = None


class WorkflowStopped(Exception):
    """Workflow dừng / bị từ chối (vd dữ liệu thiếu chỉ tiêu) ⇒ KHÔNG có số nào (03/11 "lỗi nặng ⇒ dừng hẳn"). `result` đã che mã."""
    def __init__(self, result: "runner.RunResult"):
        super().__init__("workflow không chạy xong: " + " | ".join(result.errors))
        self.result = result


def build_context(cutoff: date, workflow: dict | None = None, mask_path: str = "kpi_agent/data/ma_che_ten.json",
                  approved_gates: frozenset = frozenset()) -> KpiContext:
    """Số của backend = kết quả MỘT lượt chạy workflow khối (bước 1 S3) — trước S3 hàm này tự gọi lần lượt 5 hàm lõi; nay bộ chạy làm đúng chuỗi đó
    (S1 test: mỗi khối = hàm gốc). Đường file luật / dữ liệu lấy từ file workflow (một nguồn). `workflow` trống ⇒ file mặc định `runner.DEFAULT_WORKFLOW`."""
    workflow = workflow if workflow is not None else runner.load_workflow(runner.DEFAULT_WORKFLOW)[0]
    to_real = json.loads(Path(mask_path).read_text(encoding="utf-8"))["map"]
    result = runner.run_workflow(workflow, {"cutoff": cutoff}, {real: masked for masked, real in to_real.items()}, approved_gates)
    # "awaiting_review" = dừng ở cổng người duyệt (bước 3): số TỚI cổng đã đủ ⇒ vẫn là bản tạm tính để trang + trợ lý dùng; chỉ khối sau cổng chưa chạy
    if result.status not in ("ok", "awaiting_review"):
        raise WorkflowStopped(result)
    outputs = {port: runner.output_of(workflow, result, block_type, port) for block_type, port in
               (("load_rules", "rules"), ("read_targets", "targets"), (runner.data_block_type(workflow), "tracking"), ("check_data", "issues"),
                ("compute_kpi", "results"), ("compute_bonus", "bonuses"))}
    config = runner.repo_path(runner.node_param(workflow, "load_rules", "path"))
    rule_version = f"{config.name}@{hashlib.sha256(config.read_bytes()).hexdigest()[:8]}"
    ctx = context_from_outputs(cutoff, to_real, outputs, rule_version)
    ctx.workflow_result = result
    return ctx


def context_from_outputs(cutoff: date, to_real: dict, outputs: dict, rule_version: str) -> KpiContext:
    """Cổng ra của 6 khối tính (`rules, targets, tracking, issues, results, bonuses` — mã THẬT) ⇒ ngữ cảnh mà mọi công cụ đọc.
    Tách khỏi `build_context` (09/11) để khối agent dựng ngữ cảnh từ CHÍNH các khối đứng trước nó trong workflow — cùng một đường với backend."""
    rules, targets, tracking = outputs["rules"], outputs["targets"], outputs["tracking"]
    regions = {code: info["region"] for code, info in targets["employees"].items()}
    days_total = len(working_days(rules["period"], rules))
    days_done = len(working_days(rules["period"], rules, until=cutoff))
    issues = outputs["issues"]
    flags = _data_flags(issues, tracking, rules, to_real)
    team_issues = [issue for issue in issues if not _names_team_member(issue, to_real)]
    flags_for_model = _data_flags(team_issues, tracking, rules, to_real)
    return KpiContext(rules["period"], cutoff, rules, outputs["results"], outputs["bonuses"], regions, to_real, flags,
                      days_total, days_total - days_done, tracking.get("refreshed_at"), rule_version,
                      data_issues=_masked_data_issues(issues, to_real), data_flags_for_model=flags_for_model,
                      raw_reconciliation=tracking.get("reconciliation"))


def _names_team_member(issue: dict, to_real: dict) -> bool:
    """Cờ gắn ≥ 1 người TRONG đội — cùng cách A6 xếp `scope` "nhan_vien". Cờ chỉ có mã ngoài đội (vd "Rawdata có mã NV không có trong
    file chỉ tiêu") vẫn là cờ cả đội, vẫn vào mọi kết quả công cụ."""
    team = set(to_real.values())
    return any(code in team for code in issue.get("employees", []))


def _masked_data_issues(issues: list[dict], to_real: dict) -> list[dict]:
    """Cờ của khối Kiểm dữ liệu cho công cụ A6: câu chữ che bằng `runner.mask_codes` (mã thật NGOÀI đội — vd mã lạ trong Rawdata — thành
    "NV??", không lộ); `employee_ids` chỉ giữ mã che của người trong đội (mã ngoài đội không có mã che ⇒ bỏ, cờ đó coi là cấp đội)."""
    to_mask = {real: masked for masked, real in to_real.items()}
    return [{"level": issue["level"], "message": runner.mask_codes(issue["message"], to_mask),
             "employee_ids": sorted(to_mask[code] for code in issue.get("employees", []) if code in to_mask)} for issue in issues]


def _data_flags(issues: list[dict], tracking: dict, rules: dict, to_real: dict) -> list[str]:
    to_mask = {real: masked for masked, real in to_real.items()}
    refreshed = tracking.get("refreshed_at")
    if tracking.get("source") == "raw":
        # Đường RAW (10/11): `refreshed_at` = ngày đơn mới nhất ≤ ngày chốt (RAW không có giờ làm mới pivot)
        flags = [f"số liệu RAW DATA tới ngày đơn {refreshed:%d/%m/%Y}" if refreshed else "RAW DATA không có đơn nào tới ngày chốt"]
        # bậc 2 (bậc 1): file không có dòng "Total" ⇒ cờ cho người dùng (RAW T10 có dòng ⇒ không thêm cờ, số cờ không đổi)
        note = (tracking.get("reconciliation") or {}).get("note")
        if note:
            flags.append(note)
    else:
        flags = [f"số liệu chụp giữa tháng (pivot làm mới lần cuối {refreshed:%d/%m/%Y %H:%M}), CHƯA phải số chốt cuối tháng"
                 if refreshed else "không rõ số liệu chụp tới ngày nào"]
    # Cảnh báo của khối kiểm dữ liệu; dòng "lệch …" liệt kê từng NV — dài, chỉ giữ dòng tổng
    for issue in issues:
        if issue["level"] == "warning" or (issue["level"] == "info" and not issue["message"].startswith("lệch")):
            flags.append(_mask_codes(issue["message"], to_mask))
    # Lý do theo NGUỒN (12/11): đường RAW có từng dòng đơn — luật còn lại (số lượng tối thiểu theo từng mã) thiếu BẢNG số lượng tối thiểu, không
    # thiếu dòng đơn. Chữ cũ dùng chung cho cả hai đường làm trợ lý nói "thiếu dữ liệu từng dòng đơn" ngay trên dữ liệu từng dòng.
    reason = ("chưa có bảng số lượng tối thiểu theo từng mã hàng, đang dùng cờ đủ số lượng của phần mềm bán hàng"
              if tracking.get("source") == "raw" else "thiếu dữ liệu từng dòng đơn")
    flags += [f"luật chưa kiểm được — {reason}: {p['rule']} (công văn {p['cong_van']})"
              for p in pending_rule_status(tracking.get("source", "tracking"))]
    return flags


def _mask_codes(text: str, to_mask: dict) -> str:
    for real, masked in to_mask.items():
        text = text.replace(real, masked)
    return text


# ---------- kiểm tham số (code quyết, không để mô hình quyết) ----------

def _short(value) -> str:
    """Tham số lạ chép vào lời từ chối tối đa 40 ký tự (G4a: 5.000 ký tự ⇒ lời từ chối 5.058 ký tự, tốn token)."""
    text = repr(value)
    return text if len(text) <= 40 else text[:40] + "…"


def _check_employee(ctx: KpiContext, employee_id) -> tuple[str | None, str | None]:
    """Trả (mã thật, None) hoặc (None, lời từ chối). Chỉ nhận đúng dạng NVxx có trong đội — chặn `../`, `/`, `%2F`, mã lạ."""
    if not isinstance(employee_id, str) or not EMPLOYEE_ID_PATTERN.fullmatch(employee_id.strip().upper()):
        # Bậc Q1 (04/11, T2 — đã chọn): KHÔNG nhắc lại chữ đã gửi — có thể là mã NV THẬT (quy ước S2). Mã đúng dạng NVxx thì vẫn nói lại (dưới)
        return None, f"mã nhân viên không hợp lệ — chỉ nhận dạng NV01…NV{len(ctx.to_real):02d}"
    masked = employee_id.strip().upper()
    if masked not in ctx.to_real:
        return None, f"không có {masked} trong đội — đội có NV01…NV{len(ctx.to_real):02d}"
    return ctx.to_real[masked], None


def _check_kpi(kpi) -> tuple[str | None, str | None]:
    if kpi is None or (isinstance(kpi, str) and not kpi.strip()):
        # "KPI None" (gộp mốc 7): thiếu tham số ⇒ nói là thiếu, không in chữ "None" như một tên KPI lạ
        return None, f"thiếu tên KPI — chỉ có {', '.join(KPI_ORDER)}"
    if isinstance(kpi, str):
        for name in KPI_ORDER:
            if kpi.strip().lower() == name.lower():
                return name, None
    return None, f"KPI {_short(kpi)} không có — chỉ có {', '.join(KPI_ORDER)}"


def _check_period(ctx: KpiContext, period) -> str | None:
    """Kỳ khác kỳ đang có ⇒ từ chối, không trả số của kỳ khác thay thế (tránh agent đọc số tháng 10 thành tháng 11)."""
    if period in (None, "", ctx.period):
        return None
    return f"chưa có dữ liệu kỳ {_short(period)} — hiện chỉ có kỳ {ctx.period}"


def _shown(kpi: str, value: float | None, need_at_least: bool = False):
    """Số đưa cho mô hình: doanh số Turnover theo đồng (số nguyên), LPPC 2 chữ số lẻ, số đếm nguyên.

    Vì sao: G4b 02/11 tối — công cụ trả số thực thô (509765282.8525799) ⇒ mô hình chép nguyên "thiếu 812888711.36".
    `need_at_least`: phần còn thiếu làm tròn LÊN (thiếu 0,3 đồng vẫn là chưa đạt). Lõi tính tiền không đổi — chỉ đổi cách hiện số.
    """
    if value is None:
        return None
    if kpi == "Turnover":
        return math.ceil(value) if need_at_least else round(value)
    if kpi in ("LPPC", "LPPC_Meat"):
        # round_2dp: không dính lỗi số thực (1.1 ⇒ 1,11 ở G4a-2 — 3.8000000000000003 ⇒ 3,81 ở G4a-3).
        # Hiện thực hiện làm tròn XUỐNG: 4,6999 không được hiện 4,70 khi mốc là 4,7 (bẫy làm tròn sát mốc, tối)
        return float(round_2dp(value, ROUND_CEILING if need_at_least else ROUND_FLOOR))
    return math.ceil(value) if need_at_least else (int(value) if float(value).is_integer() else round(value, 2))


def _vn_number(value: float) -> str:
    """Số kiểu Việt cho chuỗi đưa mô hình: 812888712 ⇒ "812.888.712", 0.9 ⇒ "0,9" (tối đa 2 chữ số lẻ)."""
    # rstrip(".") : số lẻ làm tròn về ",00" (vd 23.260.710,004) từng để lại dấu phẩy treo "23.260.710," (B chụp trang bản 2)
    text = f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


RATIO_KPIS = ("LPPC", "LPPC_Meat")   # tỷ lệ dòng / đơn — chia theo ngày vô nghĩa (User câu 46: "LPPC không áp dụng" cho nhịp ngày)


def per_day_text(kpi: str, gap_raw: float, gap_shown, days_left: int) -> str | None:
    """Câu nói sẵn "còn thiếu bao nhiêu trong mấy ngày" (bậc "ít nhất N" — bản sửa sau C 14:4x).

    Nói theo TỔNG + trung bình mỗi ngày, KHÔNG làm tròn từng ngày: bản đầu "ít nhất 1 mỗi ngày" cho thiếu 3 / 10 ngày ⇒ nghĩa đen 10 cửa hàng.
    "ít nhất" chỉ khi chính số TỔNG đã làm tròn lên (vd doanh số thiếu 0,3 đồng hiện 1 đồng). Số trong câu đều có trong kết quả công cụ ⇒ có nguồn.
    
    """
    if kpi in RATIO_KPIS or not days_left or gap_shown is None:
        return None
    unit = " đồng" if kpi == "Turnover" else ""
    at_least = "ít nhất " if float(gap_shown) != float(gap_raw) else ""
    return (f"cần thêm {at_least}{_vn_number(gap_shown)}{unit} trong {days_left} ngày làm việc còn lại "
            f"(trung bình {_vn_number(gap_raw / days_left)}{unit} mỗi ngày)")


def _pct(fraction: float) -> float:
    """% thực hiện 2 chữ số lẻ, làm tròn XUỐNG — cùng hàm `floor_pct` với chữ lý do của lõi (một cách làm tròn)."""
    return float(floor_pct(fraction))


def _envelope(ctx: KpiContext, params: dict, body: dict) -> dict:
    # Bậc 4b: bản luật + trạng thái trong MỌI kết quả công cụ — để mô hình nói được "theo bản luật nào", "tạm tính" (dev 4a: A2, D1 trượt)
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "params_used": params, **body, "data_flags": ctx.data_flags_for_model,
            "rule_version": ctx.rule_version, "status": ctx.status, "status_label": ctx.status_label}


def _refused(reason: str, **received) -> dict:
    """Lời từ chối kèm tham số đã nhận (SPEC mục 4: mọi phản hồi có `params_used`) — chữ lạ cắt ≤ 40 ký tự, không số nào."""
    clipped = {key: (None if value is None else (str(value) if len(str(value)) <= 40 else str(value)[:40] + "…"))
               for key, value in received.items()}
    # Bậc Q1: chỉ nói lại mã đúng dạng NVxx (đã chuẩn hoá); chữ khác (vd mã thật, "NV01/../NV02") ⇒ None. A3 (09/11): cả hai mã của compare_employees
    for key in ("employee_id", "employee_a", "employee_b"):
        if key in received:
            value = received[key]
            normal = value.strip().upper() if isinstance(value, str) else None
            clipped[key] = normal if normal and EMPLOYEE_ID_PATTERN.fullmatch(normal) else None
    return {"refused": reason, "params_used": clipped}


def _masked_of(ctx: KpiContext) -> dict:
    return {real: masked for masked, real in ctx.to_real.items()}


def bonus_lines(ctx: KpiContext) -> list[dict]:
    """Mọi ô NV × KPI (14 × 8 = 112) bằng mã che — đầu ra lõi để lưu thành bản tính (bậc 2a) và để tính lại so từng ô."""
    return [{"employee_id": masked, "kpi": line.kpi, "pct": _pct(ctx.results[real][line.kpi].pct),
             "reached_tier": line.reached_label, "amount": line.amount, "reason": _mask_codes(line.reason, _masked_of(ctx))}
            for real, masked in sorted(_masked_of(ctx).items(), key=lambda item: item[1]) for line in ctx.bonuses[real]["lines"]]

# ---------- 4 công cụ ----------

def get_team_bonus_table(ctx: KpiContext, period: str | None = None) -> dict:
    """Bảng thưởng cả đội: mỗi NV tiền 8 KPI, % thực hiện + mốc đạt từng KPI (A2), số KPI có tiền, tổng lương kinh doanh."""
    if refusal := _check_period(ctx, period):
        return _refused(refusal, period=period)
    rows = []
    for real, masked in sorted(_masked_of(ctx).items(), key=lambda item: item[1]):
        lines = ctx.bonuses[real]["lines"]
        # A2 (nhóm): thêm % + mốc — câu "đội đứng đâu với ASO_ALL" 04/11 chạm trần vì phải gọi lẻ 14 lần get_employee_result.
        # Giữ nguyên `kpi_amounts` (owner_check đếm người có tiền từ ô này); % cùng `_pct` với get_employee_result
        rows.append({"employee_id": masked, "kpi_amounts": {line.kpi: line.amount for line in lines},
                     "kpi_pct": {line.kpi: _pct(ctx.results[real][line.kpi].pct) for line in lines},
                     "kpi_reached_tier": {line.kpi: line.reached_label for line in lines},
                     "kpis_paid": sum(1 for line in lines if line.amount > 0),
                     "business_salary": ctx.bonuses[real]["business_salary"]})
    body = {"employees": rows, "employee_count": len(rows), "kpi_count": len(KPI_ORDER),
            "team_total": sum(r["business_salary"] for r in rows),
            "business_salary_at_100_pct": ctx.rules["business_salary_total"]}
    return _envelope(ctx, {"period": ctx.period}, body)


def get_employee_result(ctx: KpiContext, employee_id: str, period: str | None = None) -> dict:
    """Một NV: 8 KPI — chỉ tiêu, thực hiện, %, mốc đạt, tiền, lý do; tổng; thưởng vượt."""
    real, refusal = _check_employee(ctx, employee_id)
    refusal = refusal or _check_period(ctx, period)
    if refusal:
        return _refused(refusal, employee_id=employee_id, period=period)
    bonus = ctx.bonuses[real]
    kpis = [{"kpi": line.kpi, "target": ctx.results[real][line.kpi].target, "actual": _shown(line.kpi, ctx.results[real][line.kpi].actual),
             "pct": _pct(ctx.results[real][line.kpi].pct), "reached_tier": line.reached_label,
             "amount": line.amount, "reason": line.reason} for line in bonus["lines"]]
    # `kpis_paid` (như bảng đội): số KPI có tiền do CODE đếm — 10/11 chạy thử Capitol câu 2 "có thưởng ở 6 KPI" bị Rào chặn vì 6 là số mô hình tự đếm
    # (đã chọn đặt ở đây — đổi vân tay bản tính, gộp chung đợt với 88a3b6f)
    body = {"employee_id": employee_id.strip().upper(), "kpis": kpis, "kpis_paid": sum(1 for line in bonus["lines"] if line.amount > 0),
            "business_salary": bonus["business_salary"],
            "over_target_bonus": bonus["over_target_bonus"], "over_target_reason": bonus["over_target_reason"]}
    return _envelope(ctx, {"employee_id": employee_id.strip().upper(), "period": ctx.period}, body)


def get_mid_month_gap(ctx: KpiContext, employee_id: str | None = None, period: str | None = None) -> dict:
    """Còn thiếu bao nhiêu để lên mốc kế tiếp (một NV hoặc cả đội), tiền thêm nếu lên mốc, ngày làm việc còn lại."""
    if refusal := _check_period(ctx, period):
        return _refused(refusal, employee_id=employee_id, period=period)
    if employee_id in (None, ""):
        reals = sorted(_masked_of(ctx), key=lambda r: _masked_of(ctx)[r])
    else:
        real, refusal = _check_employee(ctx, employee_id)
        if refusal:
            return _refused(refusal, employee_id=employee_id, period=period)
        reals = [real]
    masked_of = _masked_of(ctx)
    employees = []
    for real in reals:
        gaps = []
        for line in ctx.bonuses[real]["lines"]:
            if line.next_label is None:
                continue
            next_tier = next(t for t in kpi_tiers(ctx.rules, line.kpi, ctx.regions[real]) if t["label"] == line.next_label)
            per_day = line.gap_to_next / ctx.days_left if ctx.days_left else None
            gap_shown = _shown(line.kpi, line.gap_to_next, need_at_least=True)
            gaps.append({"kpi": line.kpi, "next_tier": line.next_label, "gap_to_next": gap_shown,
                         # TRUNG BÌNH mỗi ngày, KHÔNG làm tròn lên (bản 2, major: số làm tròn từng ngày ngược nghĩa chuỗi "trung bình");
                         # KPI tỷ lệ: không có số mỗi ngày (C 14:4x — mô hình từng nói "LPPC_Meat 0,08 mỗi ngày" từ trường này)
                         "gap_per_working_day_left": None if line.kpi in RATIO_KPIS or per_day is None else float(round_2dp(per_day, ROUND_HALF_UP)),
                         "gap_per_day_text": per_day_text(line.kpi, line.gap_to_next, gap_shown, ctx.days_left),
                         "amount_if_reached": next_tier["printed_amount"], "amount_now": line.amount,
                         # true CHỈ khi số TỔNG còn thiếu đã làm tròn lên (doanh số lẻ đồng) — trước đây true mọi khoảng (bản 2)
                         "gap_rounded_up": float(gap_shown) != float(line.gap_to_next)})
        employees.append({"employee_id": masked_of[real], "gaps": gaps})
    body = {"working_days_total": ctx.days_total, "working_days_left_after_cutoff": ctx.days_left, "employees": employees}
    params = {"employee_id": employee_id.strip().upper() if employee_id else "cả đội", "period": ctx.period}
    return _envelope(ctx, params, body)


def explain_kpi(ctx: KpiContext, employee_id: str, kpi: str, period: str | None = None) -> dict:
    """Giải thích một KPI của một NV: cách đếm, mốc của công văn, vì sao được / không được tiền."""
    real, refusal = _check_employee(ctx, employee_id)
    name, kpi_refusal = _check_kpi(kpi)
    refusal = refusal or kpi_refusal or _check_period(ctx, period)
    if refusal:
        return _refused(refusal, employee_id=employee_id, kpi=kpi, period=period)
    result = ctx.results[real][name]
    line = ctx.bonuses[real]["lines"][KPI_ORDER.index(name)]
    tiers = [{"label": t["label"], "min": t["min"], "amount": t["printed_amount"]}
             for t in kpi_tiers(ctx.rules, name, ctx.regions[real])]
    body = {"employee_id": employee_id.strip().upper(), "kpi": name, **cong_van_section(ctx, name),
            "measure": ctx.rules["kpis"][name]["measure"], "how_counted": result.how, "target": result.target,
            "actual": _shown(name, result.actual), "pct": _pct(result.pct), "tiers": tiers, "amount": line.amount,
            "reason": line.reason}
    return _envelope(ctx, {"employee_id": employee_id.strip().upper(), "kpi": name, "period": ctx.period}, body)

# nhóm A bậc mức tra cứu — chỉ đọc lõi tính sẵn có ----------

NOT_REACHED = "chưa tới mốc thấp nhất"


def get_team_kpi_summary(ctx: KpiContext, kpi: str, period: str | None = None) -> dict:
    """A1 — một KPI cho cả đội: tổng tiền, số người có / chưa có tiền, đếm người theo từng mốc + mã NV mỗi mốc.

    Vì sao: 04/11 câu "tổng tiền đội từ ASO_Focus" không công cụ nào trả (mô hình bị cấm tự cộng); câu đếm người ("cả 14 người" khi 13 / 14)
    phải để CODE đếm. Số ở đây đều cộng / đếm từ `ctx.bonuses` — cùng nguồn bảng thưởng đội, không tính lại."""
    name, refusal = _check_kpi(kpi)
    refusal = refusal or _check_period(ctx, period)
    if refusal:
        return _refused(refusal, kpi=kpi, period=period)
    index = KPI_ORDER.index(name)
    lines = {masked: ctx.bonuses[real]["lines"][index] for real, masked in _masked_of(ctx).items()}
    # Thứ tự mốc theo bảng mốc của công văn (vùng khác nhau có thể khác tiền, cùng tên mốc) — gộp theo tên, giữ thứ tự xuất hiện
    order = []
    for real in sorted(ctx.regions):
        order += [t["label"] for t in kpi_tiers(ctx.rules, name, ctx.regions[real]) if t["label"] not in order]
    by_tier = []
    for label in [None, *order]:
        codes = sorted(code for code, line in lines.items() if line.reached_label == label)
        if codes:
            by_tier.append({"reached_tier": label or NOT_REACHED, "employee_count": len(codes), "employee_ids": codes,
                            "amount_total": sum(lines[code].amount for code in codes)})
    paid = sorted(code for code, line in lines.items() if line.amount > 0)
    body = {"kpi": name, **cong_van_section(ctx, name), "employee_count": len(lines),
            "team_amount_total": sum(line.amount for line in lines.values()),
            "paid_count": len(paid), "unpaid_count": len(lines) - len(paid),
            "paid_employee_ids": paid, "unpaid_employee_ids": sorted(set(lines) - set(paid)), "by_tier": by_tier}
    return _envelope(ctx, {"kpi": name, "period": ctx.period}, body)


def _difference(value_a: int, value_b: int, code_a: str, code_b: str) -> dict:
    """Chênh lệch in sẵn: `a_minus_b` có dấu + `gap` (luôn ≥ 0) + ai hơn — mô hình chép, không tự trừ (luật "không tự tính")."""
    return {"a_minus_b": value_a - value_b, "gap": abs(value_a - value_b),
            "higher": code_a if value_a > value_b else (code_b if value_b > value_a else "bằng nhau")}


def compare_employees(ctx: KpiContext, employee_a: str, employee_b: str, kpi: str | None = None, period: str | None = None) -> dict:
    """A3 — so hai NV: mỗi bên tổng lương kinh doanh + từng KPI (hoặc chỉ một KPI) — chênh lệch tiền in sẵn (P 04/11 câu 216:
    "NV13 hơn NV11 bao nhiêu" từng phải tự trừ). Hai bên nằm trong `sides` có `employee_id` ⇒ owner_check gán đúng số cho đúng người."""
    real_a, refusal = _check_employee(ctx, employee_a)
    real_b, refusal_b = _check_employee(ctx, employee_b)
    name, kpi_refusal = (None, None) if kpi in (None, "") else _check_kpi(kpi)
    refusal = refusal or refusal_b or kpi_refusal or _check_period(ctx, period)
    if not refusal and real_a == real_b:
        refusal = "hai mã nhân viên giống nhau — cần hai người khác nhau"
    if refusal:
        return _refused(refusal, employee_a=employee_a, employee_b=employee_b, kpi=kpi, period=period)
    code_a, code_b = employee_a.strip().upper(), employee_b.strip().upper()
    kpis = [name] if name else KPI_ORDER
    sides = []
    for code, real in ((code_a, real_a), (code_b, real_b)):
        lines = ctx.bonuses[real]["lines"]
        sides.append({"employee_id": code, "business_salary": ctx.bonuses[real]["business_salary"],
                      "kpis": [{"kpi": k, "pct": _pct(ctx.results[real][k].pct), "actual": _shown(k, ctx.results[real][k].actual),
                                "reached_tier": lines[KPI_ORDER.index(k)].reached_label, "amount": lines[KPI_ORDER.index(k)].amount}
                               for k in kpis]})
    amount = lambda real, k: ctx.bonuses[real]["lines"][KPI_ORDER.index(k)].amount
    body = {"employee_a": code_a, "employee_b": code_b, "kpi": name, "sides": sides,
            "business_salary_difference": _difference(ctx.bonuses[real_a]["business_salary"], ctx.bonuses[real_b]["business_salary"],
                                                      code_a, code_b),
            "kpi_amount_differences": [{"kpi": k, **_difference(amount(real_a, k), amount(real_b, k), code_a, code_b)} for k in kpis]}
    return _envelope(ctx, {"employee_a": code_a, "employee_b": code_b, "kpi": name, "period": ctx.period}, body)


LEVEL_LABEL = {"error": "lỗi", "warning": "cảnh báo", "info": "thông tin"}


def get_data_issues(ctx: KpiContext, employee_id: str | None = None, period: str | None = None) -> dict:
    """A6 — MỌI cờ của khối Kiểm dữ liệu (lỗi / cảnh báo / thông tin), lọc theo một NV nếu có: cờ gắn NV đó + cờ cả đội.
    Một lần chụp dữ liệu — không có xu hướng theo thời gian (nói rõ trong kết quả để mô hình không suy ra "đang tốt lên / xấu đi")."""
    if refusal := _check_period(ctx, period):
        return _refused(refusal, employee_id=employee_id, period=period)
    code = None
    if employee_id not in (None, ""):
        _, refusal = _check_employee(ctx, employee_id)
        if refusal:
            return _refused(refusal, employee_id=employee_id, period=period)
        code = employee_id.strip().upper()
    issues = [{**issue, "level_label": LEVEL_LABEL[issue["level"]], "scope": "nhan_vien" if issue["employee_ids"] else "ca_doi"}
              for issue in ctx.data_issues if code is None or not issue["employee_ids"] or code in issue["employee_ids"]]
    snapshot = f"{ctx.snapshot_at:%d/%m/%Y %H:%M}" if ctx.snapshot_at else "không rõ"
    body = {"employee_id": code or "cả đội", "issues": issues, "issue_count": len(issues),
            "level_counts": {LEVEL_LABEL[level]: sum(1 for i in issues if i["level"] == level) for level in LEVEL_LABEL},
            "note": f"một lần chụp dữ liệu (pivot làm mới {snapshot}) — không có xu hướng theo thời gian"}
    return _envelope(ctx, {"employee_id": code or "cả đội", "period": ctx.period}, body)

# ---------- bảng cho trang (KHÔNG phải công cụ của agent) ----------

def team_board(ctx: KpiContext, period: str | None = None) -> dict:
    """Mọi số trang "bảng đội" cần vẽ, tính sẵn ở backend — trang không tự tính (B thiết kế).

    Vì sao endpoint riêng: `get_team_bonus_table` là kết quả công cụ agent đọc — thêm trường vào đó là đổi chữ mô hình đọc ⇒ phải đo lại agent.
    Mỗi ô NV × KPI: % (hoặc giá trị với LPPC / LPPC_Meat), tiền, trạng thái (du_moc = không còn mốc kế · moc_thap = có tiền, còn mốc kế ·
    chua_tien = 0 đ), `progress` = thực hiện / ngưỡng mốc CAO NHẤT (có thể > 1), `marks` = ngưỡng từng mốc / ngưỡng cao nhất (vạch trên thanh).
    Số hiện ra dùng cùng luật làm tròn với công cụ (`_shown`, `_pct`): % và LPPC làm tròn xuống, phần còn thiếu làm tròn lên.
    
    """
    if refusal := _check_period(ctx, period):
        return _refused(refusal, period=period)
    masked_of = _masked_of(ctx)
    employees, summary = [], {kpi: {"paid_count": 0, "full_count": 0} for kpi in KPI_ORDER}
    for real, masked in sorted(masked_of.items(), key=lambda item: item[1]):
        cells = {}
        for line in ctx.bonuses[real]["lines"]:
            result, tiers = ctx.results[real][line.kpi], kpi_tiers(ctx.rules, line.kpi, ctx.regions[real])
            by_pct = ctx.rules["kpis"][line.kpi]["measure"] == "pct_of_target"
            top = tiers[-1]["min"]
            measured = result.pct * 100 if by_pct else result.actual
            per_day = line.gap_to_next / ctx.days_left if (line.gap_to_next is not None and ctx.days_left) else None
            status = "chua_tien" if line.amount == 0 else ("du_moc" if line.next_label is None else "moc_thap")
            cells[line.kpi] = {
                "measure": "pct" if by_pct else "value",
                "pct": _pct(result.pct) if by_pct else None,
                "value": None if by_pct else _shown(line.kpi, result.actual),
                "amount": line.amount, "status": status,
                # 4 chữ số lẻ làm tròn XUỐNG: thanh không được vẽ chạm vạch mốc khi chưa thật sự tới (cùng ý _pct)
                "progress": round(float(round_2dp(measured / top * 100, ROUND_FLOOR)) / 100, 4),   # round(…, 4): bỏ đuôi số thực 0.60530000000001
                "marks": [round(tier["min"] / top, 4) for tier in tiers],
                "tier_labels": [tier["label"] for tier in tiers],
                "reached_tier": line.reached_label, "next_tier": line.next_label,
                "gap_to_next": (gap_shown := _shown(line.kpi, line.gap_to_next, need_at_least=True)),
                # cùng nghĩa với /api/gap (bậc "ít nhất N" bản sửa): trung bình không làm tròn; "ít nhất" chỉ khi TỔNG đã làm tròn lên
                "gap_per_working_day_left": None if line.kpi in RATIO_KPIS or per_day is None else float(round_2dp(per_day, ROUND_HALF_UP)),
                "gap_per_day_text": None if gap_shown is None else per_day_text(line.kpi, line.gap_to_next, gap_shown, ctx.days_left),
                "gap_rounded_up": gap_shown is not None and float(gap_shown) != float(line.gap_to_next),
                "target": result.target, "actual": _shown(line.kpi, result.actual)}
            summary[line.kpi]["paid_count"] += line.amount > 0
            summary[line.kpi]["full_count"] += status == "du_moc"
        employees.append({"employee_id": masked, "business_salary": ctx.bonuses[real]["business_salary"], "cells": cells})
    team_total = sum(row["business_salary"] for row in employees)
    team_max = len(employees) * ctx.rules["business_salary_total"]
    body = {"kpis": KPI_ORDER, "employee_count": len(employees), "team_total": team_total, "team_max_total": team_max,
            "team_share_pct": _pct(team_total / team_max) if team_max else None,
            "kpi_summary": summary, "kpis_nobody_paid": [kpi for kpi in KPI_ORDER if summary[kpi]["paid_count"] == 0],
            # trang hiện "Nam Trung Bộ" — bỏ mã vùng đứng trước " - " ("CD - Nam Trung Bộ")
            "team_label": ", ".join(sorted({region.split(" - ", 1)[-1] for region in ctx.regions.values()})),
            "working_days_total": ctx.days_total, "working_days_left_after_cutoff": ctx.days_left,
            "snapshot_at": ctx.snapshot_at.isoformat() if ctx.snapshot_at else None, "employees": employees}
    return _envelope(ctx, {"period": ctx.period}, body)


TOOLS = {"get_team_bonus_table": get_team_bonus_table, "get_employee_result": get_employee_result,
         "get_mid_month_gap": get_mid_month_gap, "explain_kpi": explain_kpi}
