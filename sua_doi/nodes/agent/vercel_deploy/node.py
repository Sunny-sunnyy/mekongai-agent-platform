"""Vercel Deploy Agent node.

Flow:
  1. Extract all input files, split into text (editable) and binary (pass-through).
  2. Run a pydantic-ai agent that receives the text file contents + a manifest of
     binary assets. The agent produces a complete website by calling
     prepare_deployment(text_files) with whatever HTML/CSS/JS/text it wants deployed.
     Any connected MCP tools are also available for extra reasoning.
  3. Merge agent text files + original binary files, then POST to Vercel directly.
     Binary files are uploaded with encoding="base64"; text with encoding="utf-8".
     The actual Vercel POST is always hardcoded — never an LLM tool.

An ``AgentBaseNode`` subclass (ENG-4437) that overrides the ``_run`` hook
with its prepare-then-deploy flow while inheriting model resolution,
MCP toolset assembly, streaming, and the execute/error skeleton. It is
the one agent node with ``SandboxMode.NEVER``: its agent only stages
text files for the hardcoded Vercel POST, so an E2B sandbox would be
pure boot latency and cost with no consumer.
"""

import asyncio
import base64
import hashlib
import logging
import mimetypes
import time
from typing import Annotated, Any

import httpx
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from core.settings import settings
from nodes.agent.vercel_deploy.params import PARAMS
from nodes.agent.vercel_deploy.ports.input import INPUT_PORTS
from nodes.agent.vercel_deploy.ports.output import OUTPUT_PORTS
from workflow.node.base.agent import AgentBaseNode, SandboxMode
try:   # [ghi chú] hằng chỉ có ở bản base node.py.ci_hop_phien (compose.ci_hop_phien.yaml); gỡ file đó ⇒ dùng CÙNG số, không ImportError
    from workflow.node.base.agent.node import TRAN_RA_MOT_LAN, TRAN_TOKEN_RA, TRAN_TOKEN_VAO
except ImportError:
    TRAN_TOKEN_VAO, TRAN_TOKEN_RA, TRAN_RA_MOT_LAN = 120_000, 16_000, 8_000   # BẰNG node.py.ci_hop_phien:59-61 và giao_dien/muc_toi_da.py
from workflow.node.base.file import NodeFile
from workflow.node.base.node import NodeOutput
from workflow.node.base.param.base import NodeParam
from workflow.node.base.param.custom import MarkdownDisplay
from workflow.node.registry import register

logger = logging.getLogger(__name__)

VERCEL_API_BASE = "https://api.vercel.com"
POLL_INTERVAL = 3

# The preparation agent only ever emits a static site (plain HTML/CSS/JS with no
# package.json or framework project). Any non-static Vercel framework preset would
# make Vercel run that framework's build against files that aren't a buildable
# project, which fails with readyState=ERROR. Until the agent can scaffold a real
# framework project, only "static" is supported: framework resolves to None (no
# build step). Unknown or legacy values (e.g. a previously-saved "nextjs"/"nuxtjs")
# fall through ``.get`` to None and are therefore coerced to static as well.
FRAMEWORK_MAP = {
    "static": None,
}

# Extensions we treat as human-readable text the agent can read and rewrite
TEXT_EXTENSIONS = {
    ".html",
    ".htm",
    ".css",
    ".js",
    ".mjs",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".xml",
    ".svg",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".env",
    ".sh",
    ".py",
}


# ---- [ghi chú] ĐĂNG TRANG VÀO KHO FILE CAPITOL (MinIO)
# thay Vercel: 0 đường ra Internet (không gọi api.vercel.com), không cần VERCEL_API_TOKEN. Vì sao gộp 1 index.html: kho đặt mỗi file 1 khoá riêng,
# đổi "/" thành "_" (services/aws/s3.py:284-323) ⇒ trang nhiều file gãy liên kết tương đối ⇒ CSS / JS nội tuyến, ảnh thành data: URI.
# An toàn (D điều kiện 1–4): đuôi CHO PHÉP (khác ⇒ TỪ CHỐI, không lặng lẽ bỏ); tên bí mật (.env*, *.key, *.pem) ⇒ từ chối NGAY khi nhận file,
# TRƯỚC lượt AI (0 đồng); trần ≤ 20 file, ≤ 2 MB / file, ≤ 5 MB tổng; thẻ CSP là PHẦN TỬ ĐẦU TIÊN của trang (trước mọi thẻ AI viết) ⇒ JS AI viết
# không chạy được (script-src 'none' — bản 2, D), không tải tài nguyên ngoài, không gửi form; meta refresh / <link> ⇒ từ chối; trang mở ở origin của MinIO (khác cổng Capitol / 18400).
import re as _re

_DUOI_DANG = {".html", ".css", ".svg", ".png", ".jpg", ".jpeg"}   # [bản 2, D đ.k. 2] bỏ .js — trang chỉ HIỂN THỊ
_TRAN_SO_FILE, _TRAN_FILE, _TRAN_TONG = 20, 2 * 1024 * 1024, 5 * 1024 * 1024
_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'none'; font-src data:; "
        "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'")
_MIME_ANH = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _ten_bi_mat(name: str) -> bool:
    t = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return t.startswith(".env") or t.endswith((".env", ".key", ".pem"))


def _chan_ten_bi_mat(names) -> None:
    xau = [n for n in names if _ten_bi_mat(n)]
    if xau:
        raise ValueError(f"Đăng trang (chay_thu): từ chối file bí mật ({len(xau)} file: .env / .key / .pem) — không đăng, không gọi AI")


def _gop_mot_trang(text_files: dict[str, str], binary_files: dict[str, bytes]) -> tuple[bytes, dict]:
    """Kiểm + gộp thành 1 index.html có CSP ở đầu. Sai ⇒ ValueError (khối lỗi, không đăng)."""
    tat_ca = {**{k: v.encode("utf-8") for k, v in text_files.items()}, **binary_files}
    _chan_ten_bi_mat(tat_ca)
    if len(tat_ca) > _TRAN_SO_FILE:
        raise ValueError(f"Đăng trang (chay_thu): {len(tat_ca)} file > trần {_TRAN_SO_FILE}")
    for n, b in tat_ca.items():
        duoi = "." + n.rsplit(".", 1)[-1].lower() if "." in n else ""
        if duoi not in _DUOI_DANG or "/" in n or "\\" in n or ".." in n:
            raise ValueError(f"Đăng trang (chay_thu): file {n[:60]!r} không được đăng (chỉ {' '.join(sorted(_DUOI_DANG))}, không thư mục)")
        if len(b) > _TRAN_FILE:
            raise ValueError(f"Đăng trang (chay_thu): file {n[:60]!r} > {_TRAN_FILE} byte")
    if sum(len(b) for b in tat_ca.values()) > _TRAN_TONG:
        raise ValueError(f"Đăng trang (chay_thu): tổng > {_TRAN_TONG} byte")
    if "index.html" not in tat_ca:
        raise ValueError("Đăng trang (chay_thu): không có index.html")
    html = tat_ca["index.html"].decode("utf-8", "replace")
    gop = 0
    for n, b in tat_ca.items():
        duoi = "." + n.rsplit(".", 1)[-1].lower()
        q = _re.escape(n)
        if duoi == ".css":
            c = b.decode("utf-8", "replace")
            if "</style" in c.lower():
                raise ValueError(f"Đăng trang (chay_thu): {n!r} chứa </style")
            html, k = _re.subn(r"<link\b[^>]*href=[\"']?(?:\./)?" + q + r"[\"']?[^>]*>", lambda _m: "<style>" + c + "</style>", html, flags=_re.I)
            gop += k
        elif duoi in _MIME_ANH:
            uri = f"data:{_MIME_ANH[duoi]};base64," + base64.b64encode(b).decode()
            html, k = _re.subn(r"(?<=[\"'(])(?:\./)?" + q + r"(?=[\"')])", uri, html)
            gop += k
    # [bản 2, D đ.k. 1] CSP KHÔNG chặn điều hướng: <meta http-equiv="refresh" content="0;url=https://…?d=…"> đưa người xem + dữ liệu ra ngoài
    # không cần JS ⇒ từ chối MỌI http-equiv trong trang AI viết (tên thuộc tính không mã hoá thực thể được; so không phân biệt hoa thường).
    # <link> còn sót sau khi gộp CSS (prefetch / dns-prefetch / preload…) ⇒ từ chối luôn.
    if _re.search(r"http\s*-\s*equiv", html, _re.I):
        raise ValueError("Đăng trang (chay_thu): trang có thẻ http-equiv (vd meta refresh tự chuyển trang) — không đăng")
    if _re.search(r"<\s*link\b", html, _re.I):
        raise ValueError("Đăng trang (chay_thu): trang còn thẻ <link> (chỉ nhận CSS gộp được) — không đăng")
    html = _re.sub(r"^\s*<!doctype[^>]*>", "", html, count=1, flags=_re.I)
    trang = ('<!DOCTYPE html><meta http-equiv="Content-Security-Policy" content="' + _CSP + '">' + html).encode("utf-8")
    if len(trang) > _TRAN_TONG * 2:
        raise ValueError("Đăng trang (chay_thu): trang gộp quá lớn")
    return trang, {"so_file_vao": len(tat_ca), "so_cho_gop": gop, "byte_trang": len(trang)}


class DeployDeps(BaseModel):
    staged_files: dict  # agent writes final text files here


async def prepare_deployment(
    ctx: RunContext[DeployDeps],
    files: Annotated[
        dict[str, str],
        (
            "Dict of {filename: text_content} for every text file to deploy "
            "(HTML, CSS, JS, etc.). Must include 'index.html'. "
            "Do NOT include binary assets — they are added automatically."
        ),
    ],
) -> str:
    """Commit the prepared text file set for deployment."""
    ctx.deps.staged_files.update(files)
    return f"Staged {len(files)} text file(s): {', '.join(files.keys())}"


def _unique_name(name: str, taken: dict[str, bytes]) -> str:
    """Return ``name``, or an ``index-2.html``-style variant if already taken.

    ``.data`` only ever yields three names, so outputs from different upstream
    nodes collide and the loser used to be dropped silently.
    """
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    counter = 2
    while f"{stem}-{counter}{dot}{ext}" in taken:
        counter += 1
    return f"{stem}-{counter}{dot}{ext}"


def _file_into(result: dict[str, bytes], name: str, raw: bytes) -> None:
    """File ``raw`` under ``name``, renaming rather than clobbering a collision.

    Shared by every inbound branch so they cannot drift apart. Bytes identical
    to what is already filed under ``name`` are skipped; identical bytes under a
    *different* name are kept, since a page may reference either.
    """
    if result.get(name) == raw:
        logger.debug("Upstream output identical to existing %r", name)
        return
    unique = _unique_name(name, result)
    if unique != name:
        logger.warning(
            "%r already claimed by another upstream output; keeping this one as %r",
            name,
            unique,
        )
    result[unique] = raw


def _is_text_file(name: str, raw: bytes) -> bool:
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in TEXT_EXTENSIONS:
        return True
    # Sniff: if the first 8KB has no null bytes, treat as text
    try:
        raw[:8192].decode("utf-8")
        return b"\x00" not in raw[:8192]
    except (UnicodeDecodeError, ValueError):
        return False


@register
class VercelDeployAgentNode(AgentBaseNode):
    version = "0.1.0"

    display_name = "Vercel Deploy"
    description = (
        "Takes text content (HTML, markdown, code) or files from an upstream agent "
        "and deploys a complete website to Vercel. An LLM prepares the site structure; "
        "binary assets are uploaded as-is. Connect MCP tools for additional reasoning."
    )
    icon = "BotMessageSquareIcon"

    show = True
    beta = False

    node_id = "vercel_deploy_agent_node"
    node_type = "agent"
    node_sub_type = "vercel_deploy"

    input_ports = INPUT_PORTS
    output_ports = OUTPUT_PORTS
    params = PARAMS

    # ------------------------------------------------------------------
    # Divergences from the AgentBaseNode (== generic Agent node) defaults
    # ------------------------------------------------------------------

    # No E2B sandbox, ever: the preparation agent only stages text files
    # via prepare_deployment and the Vercel POST is hardcoded — code
    # execution has no consumer here, so a sandbox would be pure boot
    # latency and cost. Files remain this node's DEPLOY input.
    _sandbox_mode = SandboxMode.NEVER

    # The preparation agent's prompt inlines the text-file contents and a
    # binary manifest itself — no scope/collection/citations/bad-input
    # composition, no list-tool skip.
    _scope_check_enabled = False
    _collection_context_enabled = False
    _context_graph_context_enabled = False
    _citations_prompt_enabled = False
    _bad_input_prompt_enabled = False
    _input_issue_tool_enabled = False
    _handle_tool_skiplist = frozenset()
    # Pre-ENG-4437 this node's ``include=["EXECUTE_COMPOSIO_TOOL"]`` filter
    # matched no real Composio tool, so its prep agent never saw Composio
    # tools; keep that fail-closed rather than exposing an unscoped
    # server's full tool surface (write actions included) to a deploy agent.
    _composio_passthrough_enabled = False

    # The prep agent writes staged files into DeployDeps.
    _agent_deps_type = DeployDeps

    # Streaming: shorter retry budget, chunked text emission, site-building
    # status labels.
    _stream_max_retries = 3
    _stream_base_delay = 2.0
    _stream_text_buffer_size = 80
    _stream_status_start = "Building site"
    _stream_status_delta = "Preparing site"

    _error_label = "Vercel Deploy"

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _model_settings(self) -> dict[str, Any]:
        """Vercel's LLM timeout comes from the ``llm_timeout`` param (floored
        at 120s); only temperature is otherwise configurable."""
        sp: dict[str, Any] = {}
        temp = self._validated_params.get("temperature")
        if temp is not None:
            sp["temperature"] = float(temp)
        sp["timeout"] = max(int(self._validated_params.get("llm_timeout", 120)), 120)
        sp["max_tokens"] = TRAN_RA_MOT_LAN   # [ghi chú] hook này ghi đè hook base (base kẹp 8.000 / lần, node.py.ci_hop_phien:186-191) ⇒ kẹp lại ở đây
        return sp

    def _compose_agent_tools(self) -> list:
        """The deploy-staging tool only. The actual Vercel POST is always
        hardcoded — never an LLM tool."""
        return [prepare_deployment]

    async def _pre_compile(self) -> None:
        # [chay_thu] đăng vào kho Capitol ⇒ không cần VERCEL_API_TOKEN
        self._deps = DeployDeps(staged_files={})

    async def _run(self, agent: Agent | None) -> str:

        project_name = self._validated_params.get("project_name", "") or ""
        framework_key = self._validated_params.get("framework", "static") or "static"
        framework = FRAMEWORK_MAP.get(framework_key)
        timeout = int(self._validated_params.get("deploy_timeout", 120))
        wait_for_ready = self._validated_params.get("wait_for_ready", True)

        # 1. Extract all raw files
        await self.emit_progress("Extracting input files...")
        raw_files = await self._extract_raw_files()
        if not raw_files:
            raise ValueError(
                "No content received — connect an upstream node that produces "
                "text (HTML, markdown, code) or files to the 'files' input."
            )
        _chan_ten_bi_mat(raw_files)   # [chay_thu] file bí mật ⇒ dừng TRƯỚC lượt AI (0 đồng)

        # 2. Split into text (agent can read/edit) and binary (pass-through)
        text_files: dict[str, str] = {}
        binary_files: dict[str, bytes] = {}
        for name, raw in raw_files.items():
            if _is_text_file(name, raw):
                try:
                    text_files[name] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    binary_files[name] = raw
            else:
                binary_files[name] = raw

        binary_manifest = [
            {
                "name": name,
                "mime_type": mimetypes.guess_type(name)[0]
                or "application/octet-stream",
                "size_bytes": len(raw),
            }
            for name, raw in binary_files.items()
        ]

        await self.emit_progress(
            f"Received {len(text_files)} text file(s) + {len(binary_files)} binary asset(s). "
            "Building site..."
        )

        # 3. Agent prepares the text file set (HTML/CSS/JS).
        user_prompt = self._build_preparation_prompt(text_files, binary_manifest)

        def _reset_staged_files() -> None:
            # A retried attempt must not inherit the failed attempt's
            # partially staged files.
            self._deps.staged_files = {}

        await self._stream_agent(
            agent=agent,
            run_args={
                "user_prompt": user_prompt,
                # [ghi chú] trần token như khối Agent (base node.py.ci_hop_phien:847) ⇒ có mức tối đa cho trang
                "usage_limits": UsageLimits(request_limit=settings.AGENT_REQUEST_LIMIT, input_tokens_limit=TRAN_TOKEN_VAO, output_tokens_limit=TRAN_TOKEN_RA),
            },
            on_attempt=_reset_staged_files,
        )
        staged_text = self._deps.staged_files or text_files

        # 4. Deploy: agent text files + original binary files
        await self.emit_progress(
            f"Deploying {len(staged_text)} text + {len(binary_files)} binary file(s)..."
        )
        raw_pa = self._validated_params.get("public_access")
        if raw_pa is None or raw_pa == "":
            public_access = True
        elif isinstance(raw_pa, bool):
            public_access = raw_pa
        elif isinstance(raw_pa, int):
            public_access = raw_pa != 0
        elif isinstance(raw_pa, str):
            public_access = raw_pa.lower() not in ("false", "0", "no", "off")
        else:
            public_access = bool(raw_pa)
        logger.info(f"Public access setting: raw={raw_pa!r} -> {public_access}")

        live_url, tom = await self._dang_len_kho(staged_text, binary_files)   # [chay_thu] thay _deploy (Vercel)

        self._live_url = live_url
        self._deploy_stats = {
            **tom,
            "url": live_url,
            "text_files": len(staged_text),
            "binary_files": len(binary_files),
            "project_name": project_name or "(auto)",
        }
        return f"**Deployed successfully!**\n\n🔗 {live_url}"

    def _output_data(self, result: Any) -> Any:
        return getattr(self, "_live_url", result)

    def _output_display_model(self, result: Any) -> NodeParam:
        return MarkdownDisplay(
            display_name="Deployment Result",
            field_id="vercel_deploy_result",
            required=False,
            value=result,
            info="Vercel deployment URL",
            show=True,
        )

    def _output_metadata(
        self,
        *,
        result: Any,
        output_node_files: list[NodeFile],
        input_file_names: set[str],
    ) -> dict[str, Any]:
        return dict(getattr(self, "_deploy_stats", {}) or {})

    # ------------------------------------------------------------------
    # File extraction — returns raw bytes keyed by filename
    # ------------------------------------------------------------------

    async def _extract_raw_files(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        inbound = await self._get_validated_inbound_node_output()
        for node_output in inbound.get("files", []):
            if isinstance(node_output, str) and node_output.strip():
                _file_into(result, "index.html", node_output.encode("utf-8"))
                continue
            if not isinstance(node_output, NodeOutput):
                continue
            if (
                node_output.data
                and isinstance(node_output.data, str)
                and node_output.data.strip()
            ):
                fname = "index.html"
                if node_output.data.strip().startswith(("{", "[")):
                    fname = "data.json"
                elif not any(
                    tag in node_output.data[:200].lower()
                    for tag in ("<html", "<!doctype", "<div", "<head")
                ):
                    fname = "content.md"
                _file_into(result, fname, node_output.data.encode("utf-8"))
            if node_output.files:
                for nf in node_output.files:
                    if isinstance(nf, dict):
                        try:
                            nf = NodeFile(**nf)
                        except Exception:
                            continue
                    if not isinstance(nf, NodeFile):
                        continue
                    try:
                        raw = await asyncio.to_thread(nf.download_file_content)
                    except Exception as e:
                        logger.warning(f"Could not read file {nf.name}: {e}")
                        continue
                    _file_into(result, nf.name, raw)
        return result

    # ------------------------------------------------------------------
    # Preparation prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_preparation_prompt(
        text_files: dict[str, str],
        binary_manifest: list[dict],  # [{name, mime_type, size_bytes}]
    ) -> str:
        if text_files:
            blocks = "\n\n".join(
                f"--- {name} ---\n{content}" for name, content in text_files.items()
            )
            text_section = f"TEXT FILES ({len(text_files)}):\n\n{blocks}\n\n"
        else:
            text_section = "TEXT FILES: (none)\n\n"

        if binary_manifest:
            lines = "\n".join(
                f"  • {f['name']} ({f['mime_type']}, {f['size_bytes']:,} bytes)"
                for f in binary_manifest
            )
            binary_section = f"BINARY ASSETS ({len(binary_manifest)}) — reference by filename:\n{lines}\n\n"
        else:
            binary_section = "BINARY ASSETS: (none)\n\n"

        return (
            f"{text_section}{binary_section}"
            "Build a complete website from these inputs and call prepare_deployment() "
            "with all the text files (HTML/CSS/JS) you want deployed. "
            "[chay_thu] Put EVERYTHING in ONE file index.html with CSS in <style>. NO JavaScript (scripts are blocked), no <meta http-equiv>, "
            "no <link> tags, no external URLs, fonts or CDNs — the page is a static display served offline under a strict Content-Security-Policy."
        )

    # ------------------------------------------------------------------
    # Direct Vercel deployment
    # ------------------------------------------------------------------

    async def _dang_len_kho(self, text_files: dict[str, str], binary_files: dict[str, bytes]) -> tuple[str, dict]:
        """[chay_thu] Gộp + kiểm ⇒ lên kho file Capitol theo TỔ CHỨC của lượt ⇒ link ký sẵn 1 giờ. Thiếu tổ chức ⇒ dừng."""
        from io import BytesIO

        from services.aws.s3 import S3Service

        org = str(getattr(self, "_orgid", "") or "").strip()
        if not org:
            raise ValueError("Đăng trang (chay_thu): không có mã tổ chức của lượt")
        trang, tom = _gop_mot_trang(text_files, binary_files)
        s3 = S3Service()
        kq = await asyncio.to_thread(
            s3.upload_file_object, file_obj=BytesIO(trang), filename="index.html", content_type="text/html; charset=utf-8", orgid=org,
            workflow_id=getattr(self, "_workflowid", None) or None, node_id=self.node_id or None,
            metadata={"generated_by": "chay_thu_dang_trang"}, bucket_name=settings.OUTPUTS_BUCKET_NAME)
        url = s3.generate_presigned_url(s3_key=kq["s3_key"], bucket_name=kq["s3_bucket"], expires_in=3600)
        await self.emit_progress(f"Đã đăng trang vào kho file ({tom['byte_trang']} byte, gộp {tom['so_cho_gop']} chỗ)")
        return url, {**tom, "s3_key": kq["s3_key"], "s3_bucket": kq["s3_bucket"], "sha256_trang": hashlib.sha256(trang).hexdigest()}

    async def _disable_deployment_protection(
        self,
        project_name: str,
        token: str,
    ) -> None:
        """Disable Vercel deployment protection so the site is publicly accessible."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.patch(
                    f"{VERCEL_API_BASE}/v9/projects/{project_name}",
                    headers=headers,
                    json={
                        "ssoProtection": None,
                        "passwordProtection": None,
                    },
                )
                if resp.status_code == 200:
                    await self.emit_progress(
                        "Public access enabled — no authentication required"
                    )
                else:
                    logger.warning(
                        f"Failed to disable deployment protection ({resp.status_code}): {resp.text[:200]}"
                    )
                    await self.emit_progress(
                        "Warning: could not disable auth protection — site may require Vercel login"
                    )
        except Exception as e:
            logger.warning(f"Failed to disable deployment protection: {e}")

    async def _deploy(
        self,
        text_files: dict[str, str],
        binary_files: dict[str, bytes],
        project_name: str,
        framework: str | None,
        token: str,
        timeout: int,
        wait_for_ready: bool = True,
        public_access: bool = True,
    ) -> str:
        if not project_name:
            all_names = list(text_files.keys()) + list(binary_files.keys())
            digest = hashlib.sha1("".join(sorted(all_names)).encode()).hexdigest()[:8]
            project_name = f"composer-{digest}"

        vercel_files = []
        for name, content in text_files.items():
            vercel_files.append({"file": name, "data": content, "encoding": "utf-8"})
        for name, raw in binary_files.items():
            vercel_files.append(
                {
                    "file": name,
                    "data": base64.b64encode(raw).decode("ascii"),
                    "encoding": "base64",
                }
            )

        payload = {
            "name": project_name,
            "files": vercel_files,
            "projectSettings": {"framework": framework},
            "target": "production",
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{VERCEL_API_BASE}/v13/deployments",
                params={"skipAutoDetectionConfirmation": "1"},
                headers=headers,
                json=payload,
            )

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Vercel API {resp.status_code}: {resp.text[:400]}")

        data = resp.json()
        deployment_id = data.get("id", "")
        url = data.get("url", "")

        # Disable Vercel's built-in deployment protection so the site is
        # publicly accessible without SSO or password gates.
        async with httpx.AsyncClient(timeout=15) as client:
            patch_resp = await client.patch(
                f"{VERCEL_API_BASE}/v9/projects/{project_name}",
                headers=headers,
                json={
                    "ssoProtection": None,
                    "passwordProtection": None,
                },
            )
            if patch_resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"Deployed successfully but failed to disable deployment "
                    f"protection for project '{project_name}': "
                    f"Vercel API {patch_resp.status_code}: {patch_resp.text[:400]}"
                )

        if not wait_for_ready:
            await self.emit_progress(
                f"Deployment created ({deployment_id}) — build in progress."
            )
            if public_access:
                await self._disable_deployment_protection(project_name, token)
            return f"https://{url}" if url and not url.startswith("http") else url

        state = ""
        await self.emit_progress(
            f"Deployment created ({deployment_id}) — waiting for READY..."
        )

        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=15) as client:
            while time.monotonic() < deadline:
                r = await client.get(
                    f"{VERCEL_API_BASE}/v13/deployments/{deployment_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code != 200:
                    raise RuntimeError(
                        f"Vercel API returned {r.status_code} while polling deployment"
                    )
                body = r.json()
                state = body.get("readyState", "")
                url = body.get("url", url)
                if state == "READY":
                    break
                if state in ("ERROR", "CANCELED"):
                    raise RuntimeError(f"Deployment ended with state={state}")
                await self.emit_progress(f"State: {state}...")
                await asyncio.sleep(POLL_INTERVAL)
            else:
                raise RuntimeError(
                    f"Deployment timed out after {timeout}s (last state={state})"
                )

        live_url = f"https://{url}" if url and not url.startswith("http") else url

        if public_access:
            await self._disable_deployment_protection(project_name, token)

        return live_url
