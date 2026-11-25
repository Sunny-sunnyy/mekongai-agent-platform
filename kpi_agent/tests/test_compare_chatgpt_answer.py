"""Kiểm cách đọc khuôn trả lời của ChatGPT (bảng mẫu viết tay theo khuôn trong prompt — không phải câu trả lời thật)."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare_chatgpt_answer.py"
spec = importlib.util.spec_from_file_location("compare_chatgpt_answer", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SAMPLE = """
### NV02
| KPI | Mức 100 % (đ) | Thực hiện | Mốc áp dụng | Tiền nhận (đ) | Căn cứ (mục công văn) |
|---|---|---|---|---|---|
| Turnover | 5.600.000 | 48,38 % | < 95 % ⇒ 0 | 0 | 5.2 |
| **ASO_Focus** | 1.000.000 | 94,44 % | ≥ 90 % ⇒ 80 % | 80% × 1.000.000 = 800.000 | 5.2 (2.5) |
| LPPC | 2.000.000 | 5,73 | ≥ 4,7 ⇒ mức 2 | 2,000,000 đ | 2.3 |

**NV14**
| KPI | Mức 100 % (đ) | Thực hiện | Mốc áp dụng | Tiền nhận (đ) | Căn cứ (mục công văn) |
|---|---|---|---|---|---|
| ASO_All | 1.040.000 | 85,9 % | < 100 % | 0 (chưa đạt) | 5.2 |

| Nhân viên | Tổng 8 KPI (đ) |
|---|---|
| NV02 | 5.040.000 |
| NV14 | 2.800.000 |
"""


def test_parse_answer_reads_tables_and_totals():
    amounts, totals = module.parse_answer(SAMPLE)
    assert amounts == {"NV02": {"Turnover": 0, "ASO_Focus": 800_000, "LPPC": 2_000_000}, "NV14": {"ASO_ALL": 0}}
    assert totals == {"NV02": 5_040_000, "NV14": 2_800_000}
