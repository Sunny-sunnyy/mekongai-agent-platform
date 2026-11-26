"""Bản luật THỬ cho test dùng file RAW TỰ DỰNG vài dòng (bậc 3a P2). Luật kỳ nêu mã / nhóm / ngành mà file không có dòng nào thì bộ đọc RAW dừng
(`load_raw.rule_values_without_lines`) — file thử vài dòng không thể có đủ Trà 365, mã Homey, Thịt, Bia. Test loại đó chạy với luật RAW tháng 10
THẬT (đọc lúc gọi — luật thật đổi thì test theo) + `cho_qua_khong_co_don` cho ĐÚNG các giá trị người gọi ghi rõ. Không có danh sách "cho qua hết":
mỗi file test khai giá trị mà file thử của nó thiếu. Chỉ dùng trong thư mục test — code sản phẩm không có đường nào tự thêm cho qua.
"""
import copy
from pathlib import Path

import yaml

from kpi_agent.core.rules import load_rules

ROOT = Path(__file__).resolve().parents[2]
RULES_RAW_OCT = ROOT / "kpi_agent/config/rules_2026_10_raw.yaml"
NOTE = "file thử tự dựng của test không có dòng nào mang giá trị này"


def raw_rules_allowing(*absent: str) -> dict:
    """Luật RAW tháng 10 thật + cho qua (có ghi chú) đúng các giá trị `absent`."""
    from kpi_agent.core.load_raw import period_rules
    rules = copy.deepcopy(load_rules(RULES_RAW_OCT))
    period = period_rules(rules["assumptions"])
    # Mục cho qua ghi theo KPI: một giá trị được nêu ở KPI nào của luật thật thì cho qua ở đúng các KPI đó ("Processed Meats": KPI 3 trừ + KPI 8 đếm)
    named = {"LPPC": period["lppc_excluded_subdiv"], "LPPC_Meat": (period["lppc_meat_subdiv"],),
             "ASO_Focus": period["focus"]["ASO_Focus"]["values"], "ASO_Focus_2": period["focus"]["ASO_Focus_2"]["values"]}
    entries = [{"kpi": kpi, "ma": value, "ghi_chu": NOTE} for value in absent for kpi, values in named.items() if value in values]
    assert {entry["ma"] for entry in entries} == set(absent), f"giá trị luật tháng 10 không nêu: {set(absent) - {e['ma'] for e in entries}}"
    rules["assumptions"]["cho_qua_khong_co_don"] = entries
    return rules


def write_raw_workflow_allowing(folder: Path, *absent: str) -> Path:
    """Cho test đi qua backend: ghi bản luật thử (cùng tên file với luật thật — tên file là cách backend tìm luật theo kỳ) + bản chép workflow
    đường RAW trỏ tới nó vào `folder`; trả đường dẫn workflow để đặt `main.WORKFLOW`."""
    rules_file = folder / RULES_RAW_OCT.name
    rules_file.write_text(yaml.safe_dump(raw_rules_allowing(*absent), allow_unicode=True, sort_keys=False), encoding="utf-8")
    workflow = yaml.safe_load((ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml").read_text(encoding="utf-8"))
    next(node for node in workflow["nodes"] if node["type"] == "load_rules")["params"]["path"] = str(rules_file)
    workflow_file = folder / "kpi_thuong_nvbh_raw.yaml"
    workflow_file.write_text(yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return workflow_file


def load_raw_allowing(*absent: str):
    """`load_raw` của lõi gắn sẵn bản luật thử — cho test gọi thẳng bộ đọc trên file RAW tự dựng."""
    from functools import partial

    from kpi_agent.core import load_raw as raw_reader
    return partial(raw_reader.load_raw, period_rules=raw_reader.period_rules(raw_rules_allowing(*absent)["assumptions"]))
