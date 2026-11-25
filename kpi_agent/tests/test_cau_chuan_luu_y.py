"""Bậc 3a P5 — câu chuẩn của từng lưu ý dữ liệu (`get_data_issues`, ô `sentence`): mô hình chép NGUYÊN câu code viết thì lớp kiểm cho qua (kiem_so@27
che câu đó trước các luật số); sửa mã trong câu, đổi người, thì vẫn bị chặn như trước. Kết quả công cụ dựng theo đúng hình thật (lời do code viết),
không lấy dữ liệu thật. Không gọi mô hình."""
import pytest

from kpi_agent.agent import owner_check as oc
from kpi_agent.agent.kiem_so import kiem_so

NOTE = "không thuộc MCP của chính NV (công văn 6.2: không được tính; đã loại theo luật đường RAW)"
ISSUES = {"employee_id": "cả đội", "period": "2026-10", "cutoff": "2026-10-31", "issues": [
    {"level": "info", "message": f"ASO_ALL: 4 cửa hàng {NOTE}", "employee_ids": ["NV04", "NV08", "NV14"]},
    {"level": "info", "message": f"PC_ON: 2 đơn ở cửa hàng {NOTE}", "employee_ids": ["NV08"]},
    {"level": "info", "message": f"PC_BT: 2 đơn ở cửa hàng {NOTE}", "employee_ids": ["NV04", "NV14"]},
    {"level": "info", "message": "ASO_ALL: chỉ tiêu lệch 80 % số cửa hàng MCP ở 3 NV", "employee_ids": ["NV06", "NV09", "NV11"]},
    {"level": "warning", "message": "NV có chỉ tiêu nhưng không có doanh số trong Rawdata: ['NV??']", "employee_ids": []}]}
QUESTION = "Dữ liệu tháng này có gì cần để ý không?"
SENTENCES = [oc.issue_sentence(issue) for issue in ISSUES["issues"]]
ALL = "Có mấy điều cần để ý:\n" + "\n".join(f"- {sentence}" for sentence in SENTENCES)


def check(answer: str) -> dict:
    return kiem_so({"answer": answer, "question": QUESTION, "tool_results": [ISSUES]})


def test_sentence_shape():
    assert SENTENCES[2] == f"PC_BT: 2 đơn ở cửa hàng {NOTE} — liên quan NV04, NV14."
    assert SENTENCES[4] == "NV có chỉ tiêu nhưng không có doanh số trong Rawdata: ['NV??'] — lưu ý chung của cả đội."
    assert oc.issue_sentences([ISSUES]) == SENTENCES


def test_verbatim_sentences_pass_and_need_the_mask(monkeypatch):
    result = check(ALL)
    assert result["dat"] is True and result["phien_ban"] == "kiem_so@30", result
    # Vì sao phải che (đỏ trên @26): số trong LỜI lưu ý ("80 %") đứng cạnh mã người ⇒ luật số-của-ai chặn oan câu code viết
    monkeypatch.setattr(oc, "issue_sentences", lambda tool_results: [])
    assert "so_sai_nguoi" in [problem["loai"] for problem in check(ALL)["loi"]]


@pytest.mark.parametrize("answer", [
    "- " + SENTENCES[2].replace("NV04, NV14", "NV04, NV08, NV14"),      # thêm một người vào câu (ca thật Capitol: gán thừa NV08 cho PC_BT)
    "- " + SENTENCES[1].replace("NV08", "NV04"),                         # đổi người
])
def test_edited_sentence_is_still_blocked(answer):
    assert "co_sai_nguoi" in [problem["loai"] for problem in check(answer)["loi"]]


def test_sentence_sent_along_is_not_trusted():
    # Ô `sentence` giả gửi kèm kết quả công cụ không thành nguồn: lớp kiểm tính LẠI câu từ lời + mã của lưu ý
    forged = {**ISSUES, "issues": [{**ISSUES["issues"][1], "sentence": SENTENCES[1].replace("NV08", "NV04")}]}
    result = kiem_so({"answer": "- " + SENTENCES[1].replace("NV08", "NV04"), "question": QUESTION, "tool_results": [forged]})
    assert "co_sai_nguoi" in [problem["loai"] for problem in result["loi"]]
