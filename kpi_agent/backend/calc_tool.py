"""Công cụ TÍNH cho trợ lý KPI: cộng, trừ, tỉ lệ %, tổng theo nhóm, so hai kỳ — mô hình không tự tính (, kế hoạch

Vì sao thiết kế thế này:
- **Không nhận số mô hình gõ.** Mỗi số vào là một THAM CHIẾU `{tool, args, field}` tới kết quả của một công cụ đọc. Công cụ tính TỰ CHẠY LẠI công cụ
  đó (cùng đường `_tool_for_model` của backend: cùng luật tham số, cùng bản dữ liệu) rồi lấy ô ⇒ số luôn do code tính, không thể khác số công cụ đọc
  trả. Kết quả tất định ⇒ Rào `kiem_so` chạy lại lần gọi công cụ tính cũng ra đúng số ⇒ là nguồn số hợp lệ.
  Không dùng "sổ lần gọi của lượt" (`main._record_call`): sổ đó chỉ có khi Capitol gửi mã lượt; đường chat của mình không có sổ ở backend.
- **Ghim bản dữ liệu:** số luôn đọc từ bản dữ liệu của lượt (hệ thống điền — mô hình KHÔNG có ô `data_version`, như luật ô hệ thống của Capitol
  `native_tools._KPI_O_HE_THONG`). Mô hình hay chép `period` của lần gọi trước vào `args` (lượt đo đầu 17/11: 4 / 6 câu bị công cụ nguồn từ chối) ⇒ bỏ
  ô đó, kỳ của kết quả phải đúng kỳ đã ghi. Riêng `calc_period_change`: `args.period` của mỗi bên chọn KỲ, backend tự tìm bản dữ liệu của kỳ đó.
- **Chỉ ô SỐ đã biết đơn vị** (bảng dưới): ô chữ / ô lạ ⇒ từ chối. Khác đơn vị ⇒ không cộng / trừ / chia. Không làm nguồn: `simulate_kpi_change`
  (số giả định dựng từ số mô hình gõ) và `params_used` (tham số đã gửi) — nếu không, mô hình chọn số bất kỳ rồi "rửa" qua công cụ tính.
- **Kết quả mang mã người** (`employee_id` ở từng nguồn, `employee_ids` cạnh ô `total`, `higher` là mã) theo đúng hình `owner_check` đã đọc
  (`group_totals`, `single_person_totals`, `difference_pairs`) ⇒ câu "Tổng của NV01, NV02 và NV05 là …" không bị coi là tiền của riêng NV05
  (lượt đo đầu 17/11, câu CT1).
- **Decimal**, không float (tài liệu Python `decimal`: float không biểu diễn đúng 0,1). Tiền: số nguyên đồng, làm tròn nửa lên (ROUND_HALF_UP — như
  `core/compute_bonus.py`); % : 2 số lẻ nửa lên, kèm bản 6 số lẻ. Không dùng % đã làm tròn để tính tiếp.
- Lỗi rõ, không đoán: mẫu số 0 / âm, kỳ trước 0 (không có % thay đổi), ô trống (thiếu ≠ 0), tham chiếu trùng, công cụ nguồn từ chối.
Trần: ≤ 50 tham chiếu / lần gọi; không tham chiếu tới chính công cụ tính (không lồng vòng). Không gọi AI, 0 đồng."""
import calendar
import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MAX_REFS = 50
MAX_GROUPS = 20
CALC_TOOLS = ("calc_sum", "calc_difference", "calc_percent_of", "calc_group_total", "calc_period_change")

# Đơn vị theo TÊN ô (khoá cuối của đường dẫn) — lấy từ hình kết quả 12 công cụ đọc (E dump 17/11, kiem_e/hinh_ket_qua_cong_cu_e.json)
MONEY_FIELDS = {"business_salary", "team_total", "business_salary_at_100_pct", "amount", "amount_if_reached", "amount_now", "amount_change",
                "team_amount_total", "amount_total", "a_minus_b", "over_target_bonus", "extra_bonus_total", "business_salary_8_kpi",
                "total_8_kpi_and_extra", "extra_bonus_team_total", "team_total_8_kpi", "team_total_8_kpi_and_extra",
                "spice_sales_of_customers", "reward_paid_to_customers"}
COUNT_FIELDS = {"kpis_paid", "employee_count", "kpi_count", "paid_count", "unpaid_count", "customers_in_program", "customers_reached",
                "issue_count", "opportunity_count", "working_days_total", "working_days_left_after_cutoff", "working_days_left", "working_days_done"}
PERCENT_FIELDS = {"pct"}
# Số thực hiện / chỉ tiêu / còn thiếu: đơn vị là đơn vị của KPI (hoặc khoản thưởng thêm) chứa nó
MEASURE_FIELDS = {"actual", "target", "gap_to_next"}
MONEY_KPIS = {"Turnover"}            # doanh số (đồng) cũng là tiền
RATIO_KPIS = {"LPPC", "LPPC_Meat"}   # tỉ lệ: cộng không có nghĩa
# Công cụ có số dựng từ tham số mô hình gõ (simulate: "nếu thêm extra" ⇒ scenario = thực hiện + extra) — A nhắc 17/11: number_check chỉ kiểm số có
# trong kết quả công cụ (number_check.py:17-20), nên công cụ tính không được thành cửa đổi số tự gõ thành số "có nguồn"
NOT_A_SOURCE = {"simulate_kpi_change"}
SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[([A-Za-z_]+)=([^\]]+)\])?$")
PERSON_KEYS = ("employee_id", "salesperson_in_charge")


class CalcRefused(Exception):
    """Lời từ chối gửi mô hình — chữ do code viết; chữ của mô hình cắt ≤ 40 ký tự."""


def _short(value) -> str:
    text = str(value)
    return text if len(text) <= 40 else text[:40] + "…"


def run(name: str, args: dict, call_source, current_period: str) -> dict:
    """Một lần gọi công cụ tính. `call_source(tool, args, period | None) -> dict` = chạy công cụ đọc qua đúng đường backend (None = bản của lượt;
    period = bản dữ liệu của kỳ khác — chỉ calc_period_change).
    Trả kết quả, hoặc {"refused": …} (không ném lỗi ra ngoài)."""
    try:
        reader = _Reader(call_source, current_period, allow_other_period=name == "calc_period_change")
        result = {"calc_sum": _sum, "calc_difference": _difference, "calc_percent_of": _percent_of,
                  "calc_group_total": _group_total, "calc_period_change": _period_change}[name](args, reader)
    except CalcRefused as refusal:
        return {"refused": str(refusal)}
    return {"tool": name, **result, "sources": reader.used}


class _Reader:
    """Đọc số từ tham chiếu: chạy lại công cụ nguồn (nhớ trong một lần gọi), đi theo đường dẫn, xác định đơn vị + người của số."""

    def __init__(self, call_source, current_period: str, allow_other_period: bool):
        self.call_source, self.current_period, self.allow_other_period = call_source, current_period, allow_other_period
        self.cache, self.used, self.seen = {}, [], set()

    def result_of(self, ref) -> tuple[dict, dict]:
        if not isinstance(ref, dict) or set(ref) - {"tool", "args", "field"} or not isinstance(ref.get("tool"), str) \
                or not isinstance(ref.get("field"), str) or not isinstance(ref.get("args", {}), dict):
            raise CalcRefused("mỗi số phải là tham chiếu {tool, args, field} tới kết quả một công cụ đọc — không nhận số gõ tay")
        tool = ref["tool"]
        if tool.startswith("calc_"):
            raise CalcRefused("không tham chiếu tới công cụ tính — tham chiếu thẳng tới công cụ đọc đã cho ra số")
        if tool in NOT_A_SOURCE:
            raise CalcRefused(f"{_short(tool)} không làm nguồn cho công cụ tính — số giả định của nó dựng từ số người hỏi / mô hình đưa vào")
        if ref["field"].split(".")[0].split("[")[0].strip() == "params_used":
            raise CalcRefused("params_used là tham số đã gửi, không phải số do công cụ tính ra")
        args = dict(ref.get("args") or {})
        wanted_period = args.pop("period", None)   # kỳ do hệ thống điền; giữ để đối chiếu với kỳ của kết quả
        other = str(wanted_period) if self.allow_other_period and wanted_period not in (None, "", self.current_period) else None
        key = (tool, json.dumps(args, sort_keys=True, ensure_ascii=False), other)
        if key not in self.cache:
            if len(self.cache) >= MAX_REFS:
                raise CalcRefused(f"quá {MAX_REFS} lần đọc nguồn trong một lần tính")
            self.cache[key] = self.call_source(tool, args, other)
        result = self.cache[key]
        if not isinstance(result, dict) or "refused" in result or "error" in result:
            reason = (result.get("refused") or result.get("error")) if isinstance(result, dict) else "kết quả lạ"
            raise CalcRefused(f"công cụ nguồn {_short(tool)} không cho số: {_short(reason)} — sửa args của tham chiếu (chỉ tham số của công cụ đó)")
        if wanted_period not in (None, "") and str(wanted_period) != str(result.get("period")):
            raise CalcRefused(f"tham chiếu ghi kỳ {_short(wanted_period)} nhưng dữ liệu của câu này là kỳ {_short(result.get('period'))}")
        return ref, result

    def number(self, ref) -> tuple[Decimal, str, str | None]:
        """(giá trị, đơn vị, mã người hoặc None) của MỘT tham chiếu; ghi vào danh sách nguồn đã dùng."""
        ref, result = self.result_of(ref)
        args = {k: v for k, v in (ref.get("args") or {}).items() if k != "period"}
        mark = (ref["tool"], json.dumps(args, sort_keys=True), ref["field"], str(result.get("period")))
        if mark in self.seen:
            raise CalcRefused(f"tham chiếu trùng ({_short(ref['field'])}) — mỗi số chỉ tính một lần")
        self.seen.add(mark)
        if len(self.seen) > MAX_REFS:
            raise CalcRefused(f"quá {MAX_REFS} số trong một lần tính")
        value, unit, person = value_at(result, ref["field"])
        self.used.append({"tool": ref["tool"], "args": args, "field": ref["field"], "value": _plain(value), "unit": unit,
                          **({"employee_id": person} if person else {}), "period": result.get("period"),
                          # bản dữ liệu + trạng thái (tạm tính / đã soát) của số — người đọc truy được số lấy từ bản nào
                          **{key: result[key] for key in ("ban_du_lieu", "status") if key in result}})
        return value, unit, person

    def rows(self, ref) -> tuple[list, str]:
        """Danh sách dòng mà tham chiếu trỏ tới (dùng cho tổng theo nhóm)."""
        ref, result = self.result_of(ref)
        found, _containers = _walk(result, ref["field"])
        if not isinstance(found, list) or not all(isinstance(row, dict) for row in found):
            raise CalcRefused(f"ô {_short(ref['field'])} không phải một bảng (danh sách dòng)")
        return found, ref["field"]


def _walk(result: dict, path: str) -> tuple[object, list[dict]]:
    """Đi theo đường dẫn "employees[employee_id=NV05].kpi_amounts.Turnover" ⇒ (giá trị, các bảng đã đi qua — để tìm KPI / người chứa số)."""
    current, containers = result, [result]
    for part in path.split("."):
        match = SEGMENT.fullmatch(part.strip())
        if not match or not isinstance(current, dict) or match.group(1) not in current:
            raise CalcRefused(f"không có ô {_short(part)} trong kết quả — dùng đúng tên khoá của kết quả công cụ")
        current = current[match.group(1)]
        if match.group(2):
            if not isinstance(current, list):
                raise CalcRefused(f"{_short(match.group(1))} không phải danh sách — bỏ [..=..]")
            picked = [row for row in current if isinstance(row, dict) and str(row.get(match.group(2))).upper() == match.group(3).strip().upper()]
            if len(picked) != 1:
                raise CalcRefused(f"{_short(match.group(1))}[{_short(match.group(2))}={_short(match.group(3))}]: "
                                  + ("không có dòng nào" if not picked else "nhiều hơn một dòng"))
            current = picked[0]
        if isinstance(current, dict):
            containers.append(current)
    return current, containers


def _keys(path: str) -> list[str]:
    return [SEGMENT.fullmatch(part.strip()).group(1) for part in path.split(".") if SEGMENT.fullmatch(part.strip())]


def _person(containers: list[dict]) -> str | None:
    """Mã người của số = `employee_id` (hoặc nhân viên phụ trách) của bảng gần nhất bao nó; không có ⇒ số cấp đội."""
    for box in reversed(containers):
        for key in PERSON_KEYS:
            if isinstance(box.get(key), str) and box[key].strip():
                return box[key].strip().upper()
    return None


def value_at(result: dict, path: str) -> tuple[Decimal, str, str | None]:
    value, containers = _walk(result, path)
    unit = unit_of(_keys(path), containers)   # trước kiểu số: ô chữ ⇒ lời "không phải ô số" rõ hơn
    return _as_decimal(value, path), unit, _person(containers)


def unit_of(keys: list[str], containers: list[dict]) -> str:
    last = keys[-1]
    if "kpi_amounts" in keys[:-1] or last in MONEY_FIELDS:
        return "đồng"
    if "kpi_pct" in keys[:-1] or last in PERCENT_FIELDS:
        return "%"
    if last in COUNT_FIELDS:
        return "đếm"
    if last in MEASURE_FIELDS:
        holder = next((box for box in reversed(containers) if isinstance(box.get("kpi"), str) or isinstance(box.get("item"), str)), None)
        if holder is not None and isinstance(holder.get("kpi"), str):
            return "đồng" if holder["kpi"] in MONEY_KPIS else f"chỉ số {holder['kpi']}"
        if holder is not None:
            return f"thực hiện khoản {holder['item']}"
    raise CalcRefused(f"ô {_short(last)} không phải ô số dùng được để tính (tiền, %, số đếm, thực hiện / chỉ tiêu của KPI)")


def _as_decimal(value, path: str) -> Decimal:
    if value is None:
        raise CalcRefused(f"{_short(path)} đang trống — thiếu số, không coi là 0")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalcRefused(f"{_short(path)} không phải số")
    return Decimal(str(value))   # qua chuỗi: giữ đúng số công cụ đã in (Decimal(float) mang đuôi nhị phân)


def _plain(value: Decimal):
    """Số ra JSON: nguyên ⇒ int; lẻ ⇒ float đúng chữ số đã có."""
    return int(value) if value == value.to_integral_value() else float(value)


def _money(value: Decimal) -> tuple[int, bool]:
    rounded = value.quantize(Decimal(1), ROUND_HALF_UP)
    return int(rounded), rounded != value


def _percent(part: Decimal, whole: Decimal) -> dict:
    exact = part * 100 / whole
    return {"percent": float(exact.quantize(Decimal("0.01"), ROUND_HALF_UP)),
            "percent_full": str(exact.quantize(Decimal("0.000001"), ROUND_HALF_UP)), "rounding": "2 số lẻ, nửa lên (ROUND_HALF_UP)"}


def _vn(value, unit: str) -> str:
    """Số kiểu Việt cho câu chuẩn: "37.220.000 đ", "9,28 %", "1.234" — đúng chữ số của kết quả (không làm tròn thêm)."""
    text = f"{value:,}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{text} đ" if unit == "đồng" else f"{text} %" if unit in ("%", "điểm %") else text


def _joined(codes: list[str]) -> str:
    # Mã nối bằng " + " (không dấu phẩy): owner_check cắt vế câu ở dấu phẩy ⇒ "Tổng của NV01, NV02 và NV05 là …" bị coi là tiền của NV05
    # (E thử kiem-so 17/11); câu chuẩn để cả nhóm mã nằm chung vế với số
    return " + ".join(codes)

def _addable(unit: str) -> None:
    if unit == "%":
        raise CalcRefused("không cộng %: trung bình / tổng của các % không phải % của tổng — muốn tỉ lệ cả nhóm thì cộng thực hiện, cộng chỉ tiêu "
                          "rồi dùng calc_percent_of")
    if unit.removeprefix("chỉ số ") in RATIO_KPIS:
        raise CalcRefused(f"không cộng {unit}: KPI tỉ lệ, cộng không có nghĩa")


def _total(values: list[Decimal], unit: str) -> dict:
    total = sum(values, Decimal(0))
    if unit == "đồng":
        amount, rounded = _money(total)
        return {"total": amount, **({"rounding_note": "đã làm tròn tới đồng (nửa lên)"} if rounded else {})}
    return {"total": _plain(total)}


def _people(persons: list[str | None]) -> dict:
    """`employee_ids` cạnh ô `total` khi MỌI số thuộc một người cụ thể (owner_check: tổng nhóm ≥ 2 người / tổng của đúng một người)."""
    if persons and all(persons):
        return {"employee_ids": sorted(set(persons))}
    return {}


def _sum(args: dict, reader: _Reader) -> dict:
    refs = args.get("values")
    if not isinstance(refs, list) or not 2 <= len(refs) <= MAX_REFS:
        raise CalcRefused(f"calc_sum cần values = danh sách 2–{MAX_REFS} tham chiếu")
    numbers = [reader.number(ref) for ref in refs]
    units = {unit for _value, unit, _person in numbers}
    if len(units) != 1:
        raise CalcRefused(f"khác đơn vị ({', '.join(sorted(units))}) — không cộng")
    unit = units.pop()
    _addable(unit)
    total, people = _total([value for value, _unit, _person in numbers], unit), _people([person for _value, _unit, person in numbers])
    sentences = [f"Tổng của nhóm {_joined(people['employee_ids'])}: {_vn(total['total'], unit)}."] if len(people.get("employee_ids", [])) >= 2 else []
    return {**total, "unit": unit, "item_count": len(numbers), **people, **({"sentences": sentences} if sentences else {})}


def _difference(args: dict, reader: _Reader) -> dict:
    if "a" not in args or "b" not in args:
        raise CalcRefused("calc_difference cần a và b (mỗi cái một tham chiếu) — kết quả = a − b")
    (a, unit_a, person_a), (b, unit_b, person_b) = reader.number(args["a"]), reader.number(args["b"])
    if unit_a != unit_b:
        raise CalcRefused(f"khác đơn vị ({unit_a} / {unit_b}) — không trừ")
    difference = a - b
    unit = "điểm %" if unit_a == "%" else unit_a   # 80 % ⇒ 90 % là +10 điểm phần trăm, không phải +10 %
    value = _money(difference)[0] if unit == "đồng" else _plain(difference)
    # `higher` là MÃ người khi hai bên là hai người khác nhau (owner_check.difference_pairs đọc đúng hình này của compare_employees)
    two_people = person_a and person_b and person_a != person_b
    higher = (person_a if two_people else "a") if difference > 0 else (person_b if two_people else "b") if difference < 0 else "bằng nhau"
    out = {"a_minus_b": value, "gap": abs(value), "higher": higher, "unit": unit}
    if two_people:
        lower = person_b if higher == person_a else person_a
        out["sentences"] = [f"{person_a} và {person_b} bằng nhau." if difference == 0 else f"{higher} hơn {lower} {_vn(abs(value), unit)}."]
    return out


def _percent_of(args: dict, reader: _Reader) -> dict:
    if "part" not in args or "whole" not in args:
        raise CalcRefused("calc_percent_of cần part và whole (mỗi cái một tham chiếu) — kết quả = part / whole × 100")
    (part, unit_part, person), (whole, unit_whole, _whole_person) = reader.number(args["part"]), reader.number(args["whole"])
    if unit_part != unit_whole:
        raise CalcRefused(f"khác đơn vị ({unit_part} / {unit_whole}) — không chia")
    if unit_part == "%":
        raise CalcRefused("không lấy % của % — dùng số thực hiện / chỉ tiêu gốc")
    if whole <= 0:
        raise CalcRefused("mẫu số (whole) bằng 0 hoặc âm — không có tỉ lệ")
    return {**_percent(part, whole), "input_unit": unit_part, **({"part_employee_id": person} if person else {})}


def _row_id(row: dict) -> str | None:
    for key in PERSON_KEYS:
        if isinstance(row.get(key), str):
            return row[key].strip().upper()
    return None


def _group_total(args: dict, reader: _Reader) -> dict:
    field, by, groups = args.get("value_field"), args.get("group_by"), args.get("groups")
    if not isinstance(field, str) or (by is None) == (groups is None):
        raise CalcRefused("calc_group_total cần table (tham chiếu tới một danh sách dòng), value_field (đường dẫn trong mỗi dòng) và ĐÚNG MỘT trong "
                          "group_by (tên ô nhãn của dòng) hoặc groups ({tên nhóm: [mã NV]})")
    rows, table_path = reader.rows(args.get("table"))
    if not rows:
        raise CalcRefused("bảng rỗng — không có gì để cộng")
    keys = _keys(f"{table_path}.{field}")
    values, units = [], set()
    for row in rows:
        try:
            raw, containers = _walk(row, field)
            units.add(unit_of(keys, [row, *containers]))
            values.append(_as_decimal(raw, field))
        except CalcRefused:
            values.append(None)
    if not units:
        raise CalcRefused(f"không dòng nào có số ở {_short(field)}")
    if len(units) != 1:
        raise CalcRefused(f"các dòng khác đơn vị ({', '.join(sorted(units))}) — không cộng chung")
    unit = units.pop()
    _addable(unit)
    if by is not None:
        if not isinstance(by, str):
            raise CalcRefused("group_by phải là tên một ô của dòng")
        labels = []
        for row in rows:
            try:
                label, _ = _walk(row, by)
            except CalcRefused:
                label = None
            if isinstance(label, (dict, list)):
                raise CalcRefused(f"{_short(by)} không phải ô nhãn (chữ / số) — không nhóm được")
            labels.append(label)
        names = list(dict.fromkeys(labels))
        if len(names) > MAX_GROUPS:
            raise CalcRefused(f"quá {MAX_GROUPS} nhóm — chọn ô nhóm khác")
        members = {name: [i for i, label in enumerate(labels) if label == name] for name in names}
    else:
        if not isinstance(groups, dict) or not 1 <= len(groups) <= MAX_GROUPS or \
                not all(isinstance(ids, list) and ids and all(isinstance(x, str) for x in ids) for ids in groups.values()):
            raise CalcRefused(f"groups phải là {{tên nhóm: [mã NV, …]}}, 1–{MAX_GROUPS} nhóm, mỗi nhóm ≥ 1 mã")
        listed = [x.strip().upper() for ids in groups.values() for x in ids]
        if len(listed) != len(set(listed)):
            raise CalcRefused("một nhân viên nằm ở hai nhóm — tổng sẽ bị tính hai lần")
        index = {_row_id(row): i for i, row in enumerate(rows)}
        unknown = sorted(x for x in listed if x not in index)
        if unknown:
            raise CalcRefused(f"mã không có trong bảng: {', '.join(_short(x) for x in unknown[:5])} — không tính là 0")
        members = {str(name): [index[x.strip().upper()] for x in ids] for name, ids in groups.items()}
        rest = [i for i in range(len(rows)) if i not in {j for idx in members.values() for j in idx}]
        if rest:
            members["(ngoài các nhóm)"] = rest
    out = []
    for name, idx in members.items():
        present = [values[i] for i in idx if values[i] is not None]
        ids = [_row_id(rows[i]) for i in idx]
        out.append({"group": name, **(_total(present, unit) if present else {"total": None}), "row_count": len(idx),
                    **({"employee_ids": ids} if all(ids) else {}), "missing_value_ids": [ids[k] for k, i in enumerate(idx) if values[i] is None]})
    grand = _total([value for value in values if value is not None], unit)
    if sum((group["total"] or 0) for group in out) != grand["total"]:   # mỗi dòng đúng một nhóm ⇒ tổng các nhóm = tổng bảng
        raise CalcRefused("tổng các nhóm không bằng tổng bảng — không trả số")
    reader.used.append({"tool": args["table"]["tool"], "args": {k: v for k, v in (args["table"].get("args") or {}).items() if k != "period"},
                        "field": f"{table_path}[*].{field}", "row_count": len(rows), "unit": unit})
    sentences = [f"{group['group']} ({_joined(group['employee_ids'])}): tổng {_vn(group['total'], unit)}." for group in out
                 if group.get("employee_ids") and group["total"] is not None]
    return {"groups": out, "table_total": grand["total"], "unit": unit, **({"sentences": sentences} if sentences else {}),
            "rows_missing_value": [_row_id(rows[i]) for i, value in enumerate(values) if value is None]}


def _period_end(period: str) -> date:
    year, month = (int(part) for part in period.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def _period_change(args: dict, reader: _Reader) -> dict:
    before_ref, after_ref = args.get("before"), args.get("after")
    if not all(isinstance(ref, dict) and isinstance(ref.get("args"), dict) and ref["args"].get("period") for ref in (before_ref, after_ref)):
        raise CalcRefused("calc_period_change cần before và after, mỗi cái một tham chiếu có args.period = kỳ YYYY-MM của bên đó")
    sides = []
    for ref in (before_ref, after_ref):
        _ref, result = reader.result_of(ref)
        value, unit, _person_code = reader.number(ref)
        sides.append({"period": result.get("period"), "cutoff": result.get("cutoff"), "value": value, "unit": unit})
    before, after = sides
    if not before["period"] or before["period"] == after["period"]:
        raise CalcRefused("hai tham chiếu cùng một kỳ (hoặc không rõ kỳ) — cần bản dữ liệu của hai kỳ khác nhau")
    if before["unit"] != after["unit"]:
        raise CalcRefused(f"khác đơn vị ({before['unit']} / {after['unit']}) — không so")
    for side in sides:
        try:
            side["closed"] = date.fromisoformat(str(side["cutoff"])) >= _period_end(side["period"])
        except ValueError:
            raise CalcRefused("không đọc được kỳ / ngày chốt của bản dữ liệu") from None
    warnings = ["một kỳ chưa hết, một kỳ đã chốt — so không cùng mốc (kỳ chưa hết còn thêm số)"] if before["closed"] != after["closed"] else []
    unit = "điểm %" if before["unit"] == "%" else before["unit"]
    change = after["value"] - before["value"]
    out = {"change": _money(change)[0] if unit == "đồng" else _plain(change), "unit": unit,
           "before": {"period": before["period"], "cutoff": before["cutoff"], "period_closed": before["closed"]},
           "after": {"period": after["period"], "cutoff": after["cutoff"], "period_closed": after["closed"]}, "warnings": warnings}
    if unit == "điểm %":
        out["note"] = "chênh của hai tỉ lệ là điểm phần trăm"
    elif before["value"] <= 0:
        out["percent_change"] = None
        out["note"] = "kỳ trước bằng 0 hoặc âm — không có % thay đổi, chỉ có chênh lệch"
    else:
        percent = _percent(change, before["value"])
        out.update({"percent_change": percent["percent"], "percent_change_full": percent["percent_full"]})
    return out
