"""Sáu khối TÍNH của workflow KPI + danh bạ `BLOCKS` (bước 1 S1).

Vì sao: chuỗi "luật ⇒ đọc file ⇒ kiểm ⇒ tính KPI ⇒ tính tiền" đang nằm cứng trong `backend/queries.py` `build_context`. Tách thành khối có cổng vào / ra
khai báo để bước 2 (giao diện nối khối) và bước 3 (chat soạn workflow) đọc được "có khối gì, cổng gì" từ một chỗ (học Capitol: danh bạ khối — C, KHUON mục B).
- Mỗi khối chỉ BỌC hàm sẵn có trong `kpi_agent/core/` — không viết lại phép tính (số phải ra y hệt; test so với gọi thẳng hàm gốc).
Khối làm việc với MÃ THẬT; che mã NV, lưu bản tính, cờ dữ liệu vẫn ở backend (6 khối tính).
Dữ liệu / luật hỏng ⇒ `BlockStop` (lời cho người sửa file) — bộ chạy (S2) dừng cả lượt. "lỗi nặng ⇒ DỪNG HẲN": khác backend hôm nay
  (hôm nay `check_data` báo `error` chỉ thành cờ). Tháng 10: 0 `error` ⇒ không đổi số đang thấy.
Hàm khối: `fn(inputs: dict, params: dict, run: dict) -> dict` — `inputs` theo tên cổng vào, `params` = ô cài đặt, `run` = tham số lượt chạy (vd `cutoff`)."""
from dataclasses import dataclass, field
from pathlib import Path

from kpi_agent.core.check_data import check_data as check_data_core
from kpi_agent.core.compute_bonus import employee_bonus
from kpi_agent.core.compute_kpi import compute_actuals
from kpi_agent.core.load_data import load_targets, load_tracking
from kpi_agent.core import load_tuyen
from kpi_agent.core.load_raw import load_mcp_json, load_raw, period_rules
from kpi_agent.core.rules import RulesError, load_rules as load_rules_core
from kpi_agent.agent.backend_client import TOOL_PARAMS

REPO_ROOT = Path(__file__).resolve().parents[2]


class BlockStop(Exception):
    """Khối báo dừng cả lượt (dữ liệu / luật hỏng) — lời gửi người sửa file, không phải lỗi lập trình.

    `partial` = phần kết quả khối đã có trước khi dừng (vd mọi mục kiểm dữ liệu) — bộ chạy ghi lại vào bước để người xem thấy đủ, KHÔNG chuyển
    cho khối sau (khối sau vẫn không chạy)."""
    def __init__(self, message: str, partial: dict | None = None):
        super().__init__(message)
        self.partial = partial or {}


class GateWaiting(BlockStop):
    """Khối người duyệt chưa được duyệt — lượt DỪNG Ở CỔNG, không phải lỗi (bộ chạy ghi bước `awaiting_review`, khối sau `not_run`).

    Bước 3 : không đứng chờ, không có hạn, không tự duyệt. Soát xong ⇒ chạy lại lượt
    (khối trước cổng tất định ⇒ ra y số) ⇒ cổng mở.
    """


@dataclass(frozen=True)
class Block:
    function: object
    inputs: tuple[str, ...] = ()            # cổng vào — bước 1 mọi cổng vào đều bắt buộc
    outputs: tuple[str, ...] = ()           # cổng ra
    params: dict = field(default_factory=dict)   # ô cài đặt ⇒ giá trị mặc định; REQUIRED = phải điền trong file workflow
    run_params: tuple[str, ...] = ()        # tham số lượt chạy khối cần (User: ngày chốt là tham số lượt, không nằm trong file)
    param_types: dict = field(default_factory=dict)   # ô cài đặt ⇒ kiểu nhận (S2: bài học S1 / F1 "kiểm KIỂU, không chỉ tên / có mặt")
    # Khối agent: cổng CÔNG CỤ nhận nhiều dây, mỗi dây từ một khối `kpi_tool` — dây vẫn 4 khoá (hình chung v1),
    # loại dây suy ra từ cổng đích. Bắt buộc ≥ 1 dây.
    tool_inputs: tuple[str, ...] = ()
    uses_ai: bool = False                   # khối gọi mô hình ⇒ bộ kiểm cấm dây đi ra (AI không đưa số vào khối tính tiền — #4)
    # Ô cài đặt nào là ĐƯỜNG FILE đầu vào (12/11): dấu vân tay lượt + bản tính băm NỘI DUNG mọi file khai ở đây (`runner.input_files`). Khai tường
    # minh, không dò theo tên ô. Vì sao: dấu vân tay từng chỉ băm ô `path` ⇒ file danh sách tuyến (`mcp_path` của read_raw) đổi nội dung mà bản tính
    # "đã soát" cũ vẫn được dùng lại cho số mới (ca tái hiện: 126.660.000 ⇒ 34.720.000, run_id / data_version đứng yên). Thêm ô file ⇒ khai ở đây.
    file_params: tuple[str, ...] = ()


REQUIRED = object()
# Kiểu ô: tên kiểu cho lời lỗi + hàm kiểm. bool là con của int trong Python ⇒ loại riêng (`npp_turnover_pct: true` không phải một con số)
TEXT = ("chữ khác rỗng", lambda value: isinstance(value, str) and value.strip() != "")
# % doanh số cả NPP: đơn vị là PHẦN TRĂM (105 = 105 %). Chặn khoảng 0–300 (04/11: 1000 từng lọt). Giới hạn: ghi kiểu tỉ lệ 1.05 (= 105 %)
# vẫn nằm trong khoảng ⇒ ra "NPP 1.05 % < 100 % ⇒ 0 đ" — không đoán thay người ghi được; lời nhãn ghi rõ đơn vị.
# Bảng ghép cột của file RAW (bậc 2, 17/11): đối tượng hoặc trống — khuôn chi tiết do lõi kiểm (`khuon.normalize_mapping`), sai ⇒ khối dừng
MAPPING_OR_EMPTY = ("bảng ghép cột (đối tượng) hoặc để trống", lambda value: value is None or isinstance(value, dict))
CODES_OR_EMPTY = ("danh sách mã nhân viên (chữ) hoặc để trống",
                  lambda value: value is None or (isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)))
PERCENT_OR_EMPTY = ("phần trăm 0–300 (105 = 105 %) hoặc để trống",
                    lambda value: value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 300))
# Tên công cụ nối được: một nguồn là danh sách tham số công cụ của agent (#5 — không chép danh sách). get_data_provenance đọc bảng lưu
# bản tính của backend, khối agent chưa có ⇒ chưa nối được (mục 3).
BLOCK_TOOLS = tuple(name for name in TOOL_PARAMS if name != "get_data_provenance")
TOOL_NAME = ("một trong: " + ", ".join(BLOCK_TOOLS), lambda value: value in BLOCK_TOOLS)
REASONING = ("low hoặc medium", lambda value: value in ("low", "medium"))
# Trần lần gọi công cụ / câu: 1–5 như trợ lý đang dùng (chat.MAX_TOOL_CALLS = 5) — khối không được nới trần
TOOL_CALL_LIMIT = ("số nguyên 1–5", lambda value: isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5)


# Định dạng file khối xuất được khai báo: khớp đúng 3 đường dựng file đã có (`backend/export_report.py` — to_xlsx / to_markdown / to_text)
EXPORT_FORMATS = ("xlsx", "md", "txt")
FORMAT_LIST = ("danh sách khác rỗng, không trùng, mỗi mục một trong: " + ", ".join(EXPORT_FORMATS),
               lambda value: isinstance(value, list) and value != [] and len(set(value)) == len(value)
               and all(isinstance(item, str) and item in EXPORT_FORMATS for item in value))


def _path(value: str) -> Path:
    # Đường trong file workflow tính từ gốc repo (như các đường mặc định ở backend/main.py)
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_rules(inputs: dict, params: dict, run: dict) -> dict:
    # Chỉ file YAML (scripts/run_kpi.py còn đọc được.xlsx qua core/rules_excel.py — bước 1 chưa cần)
    try:
        return {"rules": load_rules_core(_path(params["path"]))}
    except RulesError as error:
        raise BlockStop(str(error)) from error


def read_targets(inputs: dict, params: dict, run: dict) -> dict:
    return {"targets": load_targets(_path(params["path"]))}


def read_tracking(inputs: dict, params: dict, run: dict) -> dict:
    return {"tracking": load_tracking(_path(params["path"]))}


def raw_options(rules: dict) -> dict:
    """Cách đếm dòng hàng tặng (FREE ITEM FLAG = Y) theo luật — một chỗ cho khối read_raw và scripts/tinh_raw.py (hai đường phải ra cùng số)."""
    assumptions = rules["assumptions"]
    return {"lppc_exclude_free_items": assumptions.get("lppc_lines_exclude_free_items", False),
            "count_free_items": not assumptions.get("moq_lines_exclude_free_items", False),
            # mốc 7 N2 (16/11): KPI cộng dòng hoá đơn trễ (có ngày xuất) như trang sếp — thiếu khoá ⇒ bỏ như cũ
            "late_kpis": tuple(assumptions.get("late_invoice_kpis", ())),
            # mốc 7 N3 (16/11): ngành không tính là mã của PC_BT (trang sếp bỏ Bia) — thiếu khoá ⇒ không bỏ ngành nào như cũ
            "pc_bt_excluded_subdiv": tuple(assumptions.get("pc_bt_excluded_subdiv", ())),
            # bậc 1 nạp chung (17/11): trạng thái nào CẦN ngày xuất hoá đơn + ngưỡng cổng đối soát — thiếu khoá ⇒ như cũ (mọi trạng thái cần; không dừng)
            "invoice_required_status": tuple(assumptions["invoice_required_status"]) if "invoice_required_status" in assumptions else None,
            "max_excluded_sales_pct": assumptions.get("max_excluded_sales_pct")}


def read_raw(inputs: dict, params: dict, run: dict) -> dict:
    # Đường RAW DATA: cùng cổng ra `tracking` với read_tracking ⇒ khối sau không đổi. Kỳ lấy từ bản luật (RAW khác kỳ ⇒ dừng)
    try:
        # `ghep` (bậc 2 bước 2.2): bảng ghép của file tải lên (đường tải lên đặt); thiếu ⇒ bố cục RAW T10
        # bậc 3a: giá trị theo kỳ (trạng thái, hạn hoá đơn, ngành, nhóm / mã KPI 5 – 6) — thiếu khoá ⇒ ValueError ⇒ khối dừng
        tracking = load_raw(_path(params["path"]), run["cutoff"], inputs["rules"]["period"], **raw_options(inputs["rules"]), mapping=params.get("ghep"),
                            period_rules=period_rules(inputs["rules"]["assumptions"]))
    except ValueError as error:
        raise BlockStop(f"RAW DATA không dùng được: {error}") from error
    return {"tracking": {**tracking, "mcp": routes_of(params)}}


def routes_of(params: dict) -> dict:
    """Bảng tuyến của khối read_raw — MỘT chỗ đọc cho khối này và mọi màn của backend cần tuyến của một bản (thưởng thêm, điểm bán, danh sách đơn):
    đọc riêng bằng `load_mcp_json(mcp_path)` sẽ hỏng với bản lấy tuyến từ sổ. Không có `mcp_ghep` ⇒ `mcp_path` là file JSON như cũ. Có (2a-1: sổ theo dõi tải lên) ⇒ `mcp_path` là SỔ, đọc bằng
    công thức đọc `mcp_ghep`; `mcp_doi` = mã nhân viên của file chỉ tiêu của bản (đường tải lên ghi) — khối này không có cổng vào chỉ tiêu, mà thêm dây
    thì đổi hình workflow của mọi bản."""
    if params.get("mcp_ghep") is None:
        return load_mcp_json(_path(params["mcp_path"]))
    try:
        team = set(params["mcp_doi"]) if params.get("mcp_doi") is not None else None
        return load_tuyen.read_routes(_path(params["mcp_path"]), params["mcp_ghep"], team)["mcp"]
    except ValueError as error:   # RouteProblems là ValueError — lời chỉ có mã lỗi + tên trường + số dòng
        raise BlockStop(f"sổ danh sách tuyến không dùng được: {error}") from error


def check_data(inputs: dict, params: dict, run: dict) -> dict:
    issues = check_data_core(inputs["targets"], inputs["tracking"], run["cutoff"])
    errors = [issue["message"] for issue in issues if issue["level"] == "error"]
    if errors:
        # cùng luật dừng với scripts/run_kpi.py:46-48; lời có thể chứa mã NV thật — bộ chạy che trước khi ghi (thiết kế bản 2, B mục D)
        raise BlockStop("Dữ liệu thiếu thứ bắt buộc:\n" + "\n".join(errors), partial={"issues": issues})
    return {"issues": issues}


def review_gate(inputs: dict, params: dict, run: dict) -> dict:
    # Mặc định ĐÓNG: chỉ mở khi bộ chạy báo khối NÀY đã được duyệt (`run["approved"] is True` — bộ chạy điền theo id khối, backend tra bản tính đã soát).
    # Khác Capitol (D 08:22 10/11): Capitol coi quyết định rỗng là "tiếp tục". Cổng ra = ĐÚNG bảng thưởng vào — người duyệt không sửa số.
    if run.get("approved") is not True:
        raise GateWaiting("chờ duyệt — bảng thưởng chưa được soát (bấm \"Đã soát\" rồi chạy lại)")
    return {"bonuses": inputs["bonuses"]}


def export_files(inputs: dict, params: dict, run: dict) -> dict:
    # Khối xuất CHỈ KHAI BÁO "lượt này được xuất định dạng nào" — không dựng file (mục 2): khối làm việc với mã NV THẬT, còn file phải
    # dựng từ BẢN TÍNH ĐÃ LƯU (mã che) để số trên file = số trên trang. Backend chỉ trả file khi bước này `ok` — tức cổng người duyệt đứng trước đã mở.
    return {"export": {"formats": list(params["formats"])}}


def kpi_tool(inputs: dict, params: dict, run: dict) -> dict:
    # Khối công cụ chỉ KHAI BÁO "agent được dùng công cụ này" — chạy tức thì, ra đúng tên công cụ (Capitol gạt khối công cụ khỏi lượt; ở đây giữ là
    # bước thường để khỏi thêm trạng thái bước mới — ghi khác Capitol trong mục 6)
    return {"tool": params["tool"]}


def kpi_agent(inputs: dict, params: dict, run: dict) -> dict:
    # Nạp muộn: agent_block cần backend/queries, mà queries lại nạp workflow/runner ⇒ nạp ở đầu file thành vòng nạp
    from kpi_agent.workflow.agent_block import answer
    return {"answer": answer(inputs, params, run)}


def compute_kpi(inputs: dict, params: dict, run: dict) -> dict:
    return {"results": compute_actuals(inputs["targets"], inputs["tracking"], inputs["rules"])}


def compute_bonus(inputs: dict, params: dict, run: dict) -> dict:
    # Vùng của NV lấy từ file chỉ tiêu, như backend/queries.py build_context. npp_turnover_pct trống = hôm nay: thưởng vượt "thiếu dữ kiện"
    regions = {code: info["region"] for code, info in inputs["targets"]["employees"].items()}
    return {"bonuses": {code: employee_bonus(by_kpi, inputs["rules"], regions[code], params["npp_turnover_pct"])
                        for code, by_kpi in inputs["results"].items()}}


# Danh bạ khối — nguồn sự thật cho bộ kiểm file workflow (S2) và cho giao diện (bước 2). Thứ tự = thứ tự gợi ý trong file workflow (luật đầu tiên:
# lỗi luật dừng trước khi đọc 2 file Excel — B phản biện mục B).
BLOCKS = {
    "load_rules": Block(load_rules, outputs=("rules",), params={"path": REQUIRED}, param_types={"path": TEXT}, file_params=("path",)),
    "read_targets": Block(read_targets, outputs=("targets",), params={"path": REQUIRED}, param_types={"path": TEXT}, file_params=("path",)),
    "read_tracking": Block(read_tracking, outputs=("tracking",), params={"path": REQUIRED}, param_types={"path": TEXT}, file_params=("path",)),
    # Đọc RAW DATA đơn hàng (10/11) — thay read_tracking trong workflow đường RAW; MCP = file JSON tách từ trang sếp
    "read_raw": Block(read_raw, inputs=("rules",), outputs=("tracking",), params={"path": REQUIRED, "mcp_path": REQUIRED, "ghep": None, "mcp_ghep": None, "mcp_doi": None},
                      run_params=("cutoff",), param_types={"path": TEXT, "mcp_path": TEXT, "ghep": MAPPING_OR_EMPTY, "mcp_ghep": MAPPING_OR_EMPTY,
                                                          "mcp_doi": CODES_OR_EMPTY}, file_params=("path", "mcp_path")),
    "check_data": Block(check_data, inputs=("targets", "tracking"), outputs=("issues",), run_params=("cutoff",)),
    "compute_kpi": Block(compute_kpi, inputs=("targets", "tracking", "rules"), outputs=("results",)),
    "compute_bonus": Block(compute_bonus, inputs=("results", "rules", "targets"), outputs=("bonuses",), params={"npp_turnover_pct": None},
                           param_types={"npp_turnover_pct": PERCENT_OR_EMPTY}),
    # Người duyệt (bước 3): không có ô cài đặt nào — không có đường tự duyệt / hạn chờ
    "review_gate": Block(review_gate, inputs=("bonuses",), outputs=("bonuses",)),
    # Xuất file (bước 3 phần 2): nối SAU cổng người duyệt; ô formats mặc định cả 3 định dạng
    "export_files": Block(export_files, inputs=("bonuses",), outputs=("export",), params={"formats": list(EXPORT_FORMATS)},
                          param_types={"formats": FORMAT_LIST}),
    # Khối agent có công cụ (09/11): trợ lý KPI chạy như một khối — số lấy từ các khối tính đứng trước, chỉ dùng công cụ nối dây vào `tools`
    "kpi_tool": Block(kpi_tool, outputs=("tool",), params={"tool": REQUIRED}, param_types={"tool": TOOL_NAME}),
    "kpi_agent": Block(kpi_agent, inputs=("rules", "targets", "tracking", "results", "bonuses", "issues"), tool_inputs=("tools",),
                       outputs=("answer",), params={"reasoning": "low", "max_tool_calls": 5}, run_params=("cutoff", "question"),
                       param_types={"reasoning": REASONING, "max_tool_calls": TOOL_CALL_LIMIT}, uses_ai=True),
}


def param_problems(name: str, params: dict) -> list[str]:
    """Lỗi ô cài đặt của MỘT khối: ô lạ · ô bắt buộc chưa điền · sai kiểu. Một chỗ cho cả `run_block` lẫn bộ kiểm file (runner.py)."""
    block = BLOCKS[name]
    unknown = sorted(set(params) - set(block.params))
    if unknown:
        # S1 (major): ô sai tên từng bị bỏ qua im lặng ({"npp_turnover": 130} ⇒ chạy với trống) — bước 3 chat soạn file rất dễ gặp
        return [f"khối {name} không có ô: {', '.join(unknown)} (có: {', '.join(block.params) or 'không ô nào'})"]
    problems = []
    for key, default in block.params.items():
        value = params.get(key, default)
        if value is REQUIRED:
            problems.append(f"khối {name} thiếu: ô {key}")
        elif key in block.param_types and not block.param_types[key][1](value):
            problems.append(f"khối {name} ô {key} phải là {block.param_types[key][0]}, nhận {value!r}")
    return problems


def run_block(name: str, inputs: dict, params: dict | None = None, run: dict | None = None) -> dict:
    """Chạy MỘT khối (cho test và cho bộ chạy S2): điền mặc định ô cài đặt, kiểm ô (tên / bắt buộc / kiểu), đủ cổng vào, tham số lượt, kiểm khối trả đúng cổng ra."""
    block = BLOCKS[name]
    problems = param_problems(name, params or {})
    if problems:
        raise ValueError("; ".join(problems))
    filled = {key: (params or {}).get(key, default) for key, default in block.params.items()}
    missing = [f"cổng {port}" for port in block.inputs + block.tool_inputs if port not in inputs] + \
              [f"tham số lượt {key}" for key in block.run_params if key not in (run or {})]
    if missing:
        raise ValueError(f"khối {name} thiếu: {', '.join(missing)}")
    # cổng công cụ: danh sách tên công cụ (mỗi dây một tên — bộ chạy gom)
    outputs = block.function({port: inputs[port] for port in block.inputs + block.tool_inputs}, filled, run or {})
    if set(outputs) != set(block.outputs):
        raise ValueError(f"khối {name} trả {sorted(outputs)}, khai báo {list(block.outputs)}")
    return outputs
