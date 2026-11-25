import json

from eval_srvc.custom_types.enums import LanguageModel
from eval_srvc.custom_types.eval_response_fmts.reference_output_response_fmt import (
    ReferenceOutputResponseFmt,
)
from eval_srvc.custom_types.reference_input import FileRef, LoadedFile, ReferenceInput
from eval_srvc.eval._attachment_helpers import (
    NO_FABRICATION_DIRECTIVE,
    SINGLE_TURN_COMPLETION_DIRECTIVE,
)
from eval_srvc.context_vars import emit_eval_status
from eval_srvc.eval.llm_as_judge.base import BaseModelAsJudgeEval
from eval_srvc.eval.reference_output.prompt import (
    REFERENCE_FILES_ONLY_SECTION,
    REFERENCE_OUTPUT_EVAL_PROMPT,
    REFERENCE_TEXT_AND_FILES_SECTION,
    REFERENCE_TEXT_ONLY_SECTION,
    WORKFLOW_OUTPUT_FILE_SECTION,
)
from eval_srvc.infra.reference_loader import load_reference_file
from eval_srvc.llm_service.utils import parse_json_response

# Judge models allowed for reference-output (must ingest binary reference
# files). Keep in sync with the FE judge-model picker.
# [ghi chú] CHỈ gpt-6-luna (luật dự án: mọi AI dùng luna; mức evals tính theo giá luna). Trước: Claude / GPT-4o /
# gpt-5.4 / gpt-5-mini cũng nhận ⇒ request đặt judge_model khác luna chấm bằng model khác (hộp có khoá OpenAI), giá ngoài mức tối đa.
SUPPORTED_JUDGE_MODELS: frozenset[LanguageModel] = frozenset({LanguageModel.GPT_6_LUNA})


class ReferenceOutputEval(BaseModelAsJudgeEval):
    """LLM-as-judge eval that compares workflow output against a reference (text and/or files)."""

    def __init__(self, model: str | LanguageModel = LanguageModel.GPT_6_LUNA):  # [VÁ CỦA MÌNH 06/11/2026 — nhóm kỹ thuật, chay_thu/eval_service/HUONG_DAN.md] gốc CLAUDE_SONNET_46
        enum_model = model if isinstance(model, LanguageModel) else LanguageModel(model)
        if enum_model not in SUPPORTED_JUDGE_MODELS:
            allowed = ", ".join(sorted(m.value for m in SUPPORTED_JUDGE_MODELS))
            raise ValueError(
                f"Judge model '{enum_model.value}' is not supported for the "
                f"reference-output eval. Pick one of: {allowed}."
            )
        super().__init__(model=enum_model.value)

    @staticmethod
    def _build_reference_section(
        reference_text: str, loaded_files: list[LoadedFile]
    ) -> str:
        if loaded_files and not reference_text:
            return REFERENCE_FILES_ONLY_SECTION.format(
                file_count=len(loaded_files),
                file_list=", ".join(f.file_name for f in loaded_files),
            )
        if loaded_files and reference_text:
            return REFERENCE_TEXT_AND_FILES_SECTION.format(
                reference_text=reference_text,
                file_count=len(loaded_files),
                file_list=", ".join(f.file_name for f in loaded_files),
            )
        return REFERENCE_TEXT_ONLY_SECTION.format(reference_text=reference_text)

    async def evaluate(
        self,
        content: str | list | dict,
        meta_data: dict | None = None,
    ) -> ReferenceOutputResponseFmt:
        meta_data = meta_data or {}
        reference_input: ReferenceInput | None = meta_data.get("reference_input")

        if reference_input is None:
            raise ValueError(
                "ReferenceOutputEval requires `reference_input` in meta_data"
            )
        # [ghi chú] Bộ chạy thử: CHỈ chấm chữ. File (reference_input.files / workflow_output_file) đi load_reference_file
        # (reference_loader.py:45) tải từ S3 — tới 32 MB / file, không giới hạn số file, không qua _KHOA_FILE của eval_runner ⇒ S3 sống là file thật
        # tới mô hình, tiền ngoài mức tối đa. Chặn TRƯỚC mọi lần tải / gọi AI. Chữ: cùng trần byte như eval_runner (đo đúng chuỗi json.dumps).
        if getattr(reference_input, "files", None) or meta_data.get("workflow_output_file") or meta_data.get("workflow_output_files"):
            raise ValueError("Bộ chạy thử: bài chấm theo đáp án mẫu chỉ nhận chữ — không nhận file đính kèm (reference_input.files / workflow_output_file).")
        import json as _json
        _n = len(_json.dumps({"content": content, "reference": getattr(reference_input, "text", None) or ""}, default=str).encode())
        if _n > 60_000:   # BẰNG eval_runner.EVAL_DU_LIEU_BYTE
            raise ValueError(f"Bộ chạy thử: dữ liệu chấm {_n} byte vượt trần 60000 — không cắt.")

        loaded_ref_files = [load_reference_file(f) for f in reference_input.files] if reference_input.files else []
        reference_text = reference_input.text or ""

        workflow_output_file_ref: FileRef | None = meta_data.get(
            "workflow_output_file"
        )
        loaded_wo_files: list[LoadedFile] = []
        if workflow_output_file_ref:
            loaded_wo_files = [load_reference_file(workflow_output_file_ref)]

        if loaded_wo_files:
            formatted_workflow_output = WORKFLOW_OUTPUT_FILE_SECTION.format(
                file_count=len(loaded_wo_files),
                file_list=", ".join(f.file_name for f in loaded_wo_files),
            )
        else:
            formatted_workflow_output = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2)
            )

        all_attachments = loaded_ref_files + loaded_wo_files

        reference_section = self._build_reference_section(
            reference_text, loaded_ref_files
        )
        prompt = REFERENCE_OUTPUT_EVAL_PROMPT.format(
            reference_section=reference_section,
            workflow_output=formatted_workflow_output,
        )
        if all_attachments:
            prompt += SINGLE_TURN_COMPLETION_DIRECTIVE
        prompt += NO_FABRICATION_DIRECTIVE

        await emit_eval_status("Running reference output evaluation...")

        response = await self.llm.aquery(
            prompt,
            attachments=all_attachments or None,
            timeout=1800,
        )
        if response.stop_reason not in ("stop", "tool_call", None):
            raise ValueError(
                f"ReferenceOutputEval: incomplete response "
                f"(stop_reason={response.stop_reason!r})"
            )
        response_json = parse_json_response(response.content)

        return ReferenceOutputResponseFmt(**response_json, thinking=response.thinking)
