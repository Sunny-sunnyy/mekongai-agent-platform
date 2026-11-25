# Image tu dung cho may chu MCP Exa (capitol-mcp-exa)
# Dung tu thu muc goc kho ban giao:
#   docker build -t capitol-local/mcp-exa:latest -f docker_build/mcp-exa/Dockerfile docker_build/mcp-exa
FROM python:3.12-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml requirements-exa.txt /app/

RUN uv venv /app/.venv \
 && uv pip install --python /app/.venv/bin/python --no-cache -r /app/requirements-exa.txt \
 && rm -rf /root/.cache/uv

ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 1000:1000

ENTRYPOINT ["python", "-I", "/thu/may_chu.py"]
