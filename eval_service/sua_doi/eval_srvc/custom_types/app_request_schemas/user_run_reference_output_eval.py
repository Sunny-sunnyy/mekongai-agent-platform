from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from eval_srvc.custom_types.enums import LanguageModel
from eval_srvc.custom_types.reference_input import FileRef, ReferenceInput


class UserRunReferenceOutputEval(BaseModel):
    workflow_output: str
    workflow_output_file: FileRef | None = None
    reference_input: ReferenceInput
    judge_model: LanguageModel = Field(default=LanguageModel.GPT_6_LUNA)  # [VÁ CỦA MÌNH 06/11/2026 — nhóm kỹ thuật, chay_thu/eval_service/HUONG_DAN.md] gốc CLAUDE_SONNET_46
    eval_name: str | None = Field(
        default=None, description="Name of the eval definition."
    )
    eval_id: UUID = Field(
        description="ID of the eval definition from the evaluations table."
    )
    name: str = Field(
        default="reference-output-eval",
        description="Suite name for OpenSearch persistence.",
    )
    suite_id: UUID = Field(
        default_factory=uuid4,
        validation_alias="id",
        description="Suite ID for OpenSearch persistence.",
    )
    org_id: UUID | None = Field(
        description="UUID for organization making the request.", default=None
    )

    @model_validator(mode="after")
    def _populate_eval_defaults(self) -> "UserRunReferenceOutputEval":
        if self.eval_name is None:
            self.eval_name = self.name
        return self
