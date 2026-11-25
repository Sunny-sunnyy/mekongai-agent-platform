import asyncio
import logging
from abc import ABC, abstractmethod
from io import BytesIO

from sentry_sdk import capture_exception

from api.routers.models.files import FileUploadResponse
from core.settings import settings
from nodes.redaction.params import PDF_REDACTION_PARAMS, TEXT_REDACTION_PARAMS
from nodes.redaction.ports import (
    PDF_REDACTION_INPUT_PORTS,
    PDF_REDACTION_OUTPUT_PORTS,
    TEXT_REDACTION_INPUT_PORTS,
    TEXT_REDACTION_OUTPUT_PORTS,
)
from services.aws.s3 import S3Service
from services.redaction.enums import RedactionType
from services.redaction.redaction_service import RedactionService
from services.redaction.schemas import RedactionConfig, RedactionCustomConfig
from workflow.node.base.file import NodeFile
from workflow.node.base.node import BaseNode, NodeOutput
from workflow.node.base.param.custom import MarkdownDisplay
from workflow.node.registry import register

# Set up logger and configure log level
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BaseRedactionNode(BaseNode, ABC):
    version = "0.1.0"

    description = "Removes or masks sensitive information based on custom user rules or instructions"
    icon = "TextIcon"

    show = True
    beta = False

    node_type = "governance"
    node_sub_type = "redaction"

    # subclasses override.
    display_name = "Text Redaction"
    node_id = "text_redaction_node"
    input_ports = TEXT_REDACTION_INPUT_PORTS
    output_ports = TEXT_REDACTION_OUTPUT_PORTS
    params = TEXT_REDACTION_PARAMS

    def __init__(
        self,
        node_config: list[dict],
        node_inputs=None,
        validate_required=True,
        validate_type=False,
    ) -> None:
        """Initialize the redaction node."""
        super().__init__(
            node_config=node_config,
            node_inputs=node_inputs,
            validate_required=validate_required,
            validate_type=validate_type,
        )
        self._validated_params = {}
        self._service = RedactionService()

    @abstractmethod
    async def _redact(self, redaction_config: RedactionConfig) -> NodeOutput: ...

    async def execute(self) -> NodeOutput:
        """Execute the redaction node."""

        try:
            await self.emit_begin()

            self._validated_params = await self._get_validated_params()

            redaction_type = self._validated_params.get("redaction_type")
            redaction_name = self._validated_params.get("redaction_name")
            custom_instructions = self._validated_params.get("custom_instructions")

            if redaction_type == RedactionType.CUSTOM:
                await self.emit_progress("Redacting content using custom instructions.")
                redactions = [
                    RedactionCustomConfig(custom_instructions=custom_instructions)
                ]

            else:
                raise ValueError("Redaction type is not supported")

            # create redaction config
            redaction_config = RedactionConfig(
                name=redaction_name,
                redaction_type=redaction_type,
                redactions=redactions,
            )

            output = await self._redact(redaction_config)

            return output

        except Exception as e:
            capture_exception(e)
            error_msg = f"Redaction node failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            await self.emit_error({"error": error_msg, "details": str(e)})
            await self.emit_end({"error": error_msg})
            return NodeOutput(data=None, node=self, error=error_msg)


@register
class TextRedactionNode(BaseRedactionNode):
    input_ports = TEXT_REDACTION_INPUT_PORTS
    output_ports = TEXT_REDACTION_OUTPUT_PORTS
    params = TEXT_REDACTION_PARAMS

    display_name = "Text Redaction"
    node_id = "text_redaction_node"

    async def _redact(self, redaction_config: RedactionConfig) -> NodeOutput:
        data = self._validated_params.get("data")

        inbound_outputs = await self._get_validated_inbound_node_output()
        inbound_data_outputs = inbound_outputs.get("data", [])

        text_inputs: list[str] = []

        if inbound_data_outputs:
            for inbound in inbound_data_outputs:
                if not isinstance(inbound, NodeOutput):
                    continue
                if inbound.data is not None:
                    text_inputs.append(str(inbound.data))
        else:
            # Fallback: no inbound node outputs available (e.g., manual entry).
            if isinstance(data, list) and all(isinstance(item, str) for item in data):
                text_inputs.extend(data)
            elif isinstance(data, str):
                text_inputs.append(data)

        # JSON behavior remains "redact as JSON" when the inbound payload is JSON.
        if isinstance(data, dict) or (
            isinstance(data, list) and all(isinstance(item, dict) for item in data)
        ):
            await self.emit_progress(
                "JSON input recognized. Redacting content from JSON"
            )
            result = await self._service.redact_json(
                data, redaction_config, orgid=self._orgid
            )
            preview_sections: list[tuple[str, str]] = [("JSON", str(result))]
            emit_values: list[str] = [str(result)]
        else:
            combined_text = "\n".join(text_inputs).strip() if text_inputs else ""

            await self.emit_progress(f"{len(text_inputs)} text input(s) recognized.")

            tasks: list[tuple[str, asyncio.Future]] = []
            if combined_text:
                tasks.append(
                    (
                        "Combined Text Inputs",
                        asyncio.ensure_future(
                            self._service.redact_text(
                                combined_text, redaction_config, orgid=self._orgid
                            )
                        ),
                    )
                )

            await self.emit_progress("Running redactions concurrently.")
            if tasks:
                redacted_values = await asyncio.gather(*[t[1] for t in tasks])
            else:
                redacted_values = []

            preview_sections = [
                (title, redacted)
                for (title, _), redacted in zip(tasks, redacted_values)
            ]
            emit_values = [redacted for _, redacted in preview_sections]

            if not emit_values:
                emit_values = [""]
                preview_sections = [("Redaction Result", "")]

            # For backwards compatibility with callers expecting a single string, keep
            # `data` as a string when only one output was produced.
            result = emit_values[0] if len(emit_values) == 1 else emit_values

        await self.emit_success(result)

        if isinstance(result, list):
            display_value = "\n\n".join(
                f"# {title}\n\n{body}" for title, body in preview_sections
            )
        else:
            display_value = f"# {preview_sections[0][0]}\n\n{preview_sections[0][1]}"

        output = NodeOutput(
            data=result,
            node=self,
            display_model=MarkdownDisplay(
                display_name="Redaction Result",
                field_id="redaction_result",
                required=True,
                value=display_value,
                info="Redaction Result",
                show=True,
            ),
        )

        await self.emit_node_result(output)

        # Emit to output port if runtime is available
        if hasattr(self, "runtime") and self.runtime and result:
            for port in self.output_ports:
                for item in emit_values:
                    await self.runtime.emit_output(port.id, item)

        await self.emit_end()

        return output


@register
class PDFRedactionNode(BaseRedactionNode):
    input_ports = PDF_REDACTION_INPUT_PORTS
    output_ports = PDF_REDACTION_OUTPUT_PORTS
    params = PDF_REDACTION_PARAMS

    display_name = "PDF Redaction"
    node_id = "pdf_redaction_node"

    async def _progress_callback(self, pdf_name: str, payload: dict):
        event = payload.get("event")
        if event != "term_extraction_milestone":
            return

        pct = payload.get("pct")
        if pct not in (25, 50, 75, 100):
            return

        await self.emit_progress(
            f"Redaction term extraction {pct}% complete ({pdf_name})"
        )

    async def _process_one_pdf(
        self, pdf_file: NodeFile, redaction_config: RedactionConfig
    ) -> tuple[NodeFile, dict]:
        s3_service = S3Service()
        s3_service.bucket_name = settings.resolve_s3_bucket(generated=True)

        pdf_name = pdf_file.name or "document.pdf"

        if pdf_name.lower().endswith(".pdf"):
            output_filename = f"{pdf_name[:-4]}_redacted.pdf"
        else:
            output_filename = f"{pdf_name}_redacted.pdf"

        pdf_bytes = await asyncio.to_thread(pdf_file.download_file_content)

        async def _per_pdf_progress_callback(payload: dict):
            await self._progress_callback(pdf_name, payload)

        _canh_bao: list[str] = []   # [ghi chú] ảnh không che được ⇒ đưa vào ĐẦU RA (dịch vụ điền)
        redacted_pdf_bytes = await self._service.redact_pdf(
            pdf_bytes=pdf_bytes,
            redaction=redaction_config,
            progress_callback=_per_pdf_progress_callback,
            orgid=self._orgid,
            canh_bao=_canh_bao,
        )
        self._canh_bao_che = getattr(self, "_canh_bao_che", []) + [f"{pdf_name}: {c}" for c in _canh_bao]

        upload_result = await asyncio.to_thread(
            s3_service.upload_file_object,
            file_obj=BytesIO(redacted_pdf_bytes),
            filename=output_filename,
            content_type="application/pdf",
            orgid=self._orgid or "unknown",
            workflow_id=self._workflowid or None,
            node_id=self.node_id or None,
            metadata={
                "generated_by": "redaction_node",
                "source_s3_key": pdf_file.s3_key,
                "redaction_name": redaction_config.name,
            },
        )

        download_url = s3_service.generate_presigned_url(
            s3_key=upload_result["s3_key"],
            bucket_name=upload_result["s3_bucket"],
        )

        redacted_file = NodeFile(
            name=output_filename,
            presigned_url=download_url,
            mime_type="application/pdf",
            s3_bucket=upload_result["s3_bucket"],
            s3_key=upload_result["s3_key"],
            s3_region=upload_result["s3_region"],
            generated=True,
        )
        return redacted_file, {
            "output_s3_key": upload_result["s3_key"],
            "output_s3_bucket": upload_result["s3_bucket"],
            "output_filename": output_filename,
            "source_s3_key": pdf_file.s3_key,
        }

    async def _redact(self, redaction_config: RedactionConfig) -> NodeOutput:
        self._canh_bao_che = []   # [va_che_pdf vòng 3b] đặt lại mỗi lần chạy
        inbound_outputs = await self._get_validated_inbound_node_output()
        inbound_data_outputs = inbound_outputs.get("data", [])
        inbound_pdf_files: list[NodeFile] = []
        for inbound in inbound_data_outputs:
            if not isinstance(inbound, NodeOutput) or not inbound.files:
                continue
            for file in inbound.files:
                if isinstance(file, NodeFile) and file.mime_type == "application/pdf":
                    inbound_pdf_files.append(file)

        if not inbound_pdf_files:
            data = self._validated_params.get("data")
            if data and isinstance(data, dict):
                try:
                    file_response = FileUploadResponse(**data)
                    file_info = file_response.file
                    if file_info and file_info.content_type == "application/pdf":
                        inbound_pdf_files.append(
                            NodeFile(
                                name=file_info.original_filename or file_info.filename,
                                presigned_url=file_response.download_url,
                                mime_type=file_info.content_type,
                                s3_bucket=file_info.s3_bucket,
                                s3_key=file_info.s3_key,
                                s3_region=file_info.s3_region,
                            )
                        )
                except Exception as e:
                    logger.warning("Failed to parse direct file upload: %s", e)

        await self.emit_progress(
            f"{len(inbound_pdf_files)} PDF input(s) recognized. Redacting PDF(s)."
        )

        results = await asyncio.gather(
            *[self._process_one_pdf(f, redaction_config) for f in inbound_pdf_files],
            return_exceptions=True,
        )

        redacted_files: list[NodeFile] = []
        per_file_metadata: list[dict] = []
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(
                    "Failed to redact PDF %s (%s): %s",
                    idx,
                    getattr(inbound_pdf_files[idx], "name", "unknown"),
                    str(res),
                    exc_info=True,
                )
                continue
            file, metadata = res
            redacted_files.append(file)
            per_file_metadata.append(metadata)

        if not redacted_files:
            if not inbound_pdf_files:
                raise RuntimeError("No PDF files found. Please upload a PDF.")
            failed_names = [
                getattr(inbound_pdf_files[i], "name", f"PDF {i}")
                for i in range(len(results))
                if isinstance(results[i], Exception)
            ]
            raise RuntimeError(
                f"PDF redaction failed for: {', '.join(failed_names)}. "
                "Check that the uploaded files are valid PDFs."
            )

        urls = [f.presigned_url for f in redacted_files if f.presigned_url]
        result_text = "Redacted PDF(s) generated:\n" + "\n".join(urls)
        # [ghi chú] cảnh báo nằm TRONG chữ đầu ra (cùng chỗ "Redacted"): ảnh không che + PDF bị từ chối (trước đây chỉ log ⇒
        # người dùng thấy "Redacted" mà không biết ảnh còn nguyên / có file bị bỏ).
        _cb = list(getattr(self, "_canh_bao_che", []))
        _hong = [getattr(inbound_pdf_files[i], "name", f"PDF {i}") for i in range(len(results)) if isinstance(results[i], Exception)]
        if _hong:
            _cb.append(f"{len(_hong)} PDF KHÔNG được che và KHÔNG có trong kết quả: {', '.join(_hong)}.")
        if _cb:
            result_text = "⚠ CẢNH BÁO CHE PDF:\n- " + "\n- ".join(_cb) + "\n\n" + result_text
        await self.emit_success(result_text)

        output = NodeOutput(
            files=redacted_files,
            data=result_text,
            node=self,
            display_model=MarkdownDisplay(
                display_name="Redaction Result",
                field_id="redaction_result",
                required=True,
                value=result_text,
                info="Redaction Result",
                show=True,
            ),
            metadata={"files": per_file_metadata},
        )

        await self.emit_node_result(output)

        if hasattr(self, "runtime") and self.runtime:
            for port in self.output_ports:
                await self.runtime.emit_output(port.id, output)

        await self.emit_end()

        return output
