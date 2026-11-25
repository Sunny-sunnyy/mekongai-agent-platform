# Image tu dung cho kpi-backend (moc dong bang 252dc30a)
# Dung tu thu muc goc kho ban giao:
#   docker build -t capitol-local/kpi-backend:252dc30a -f docker_build/kpi-backend/Dockerfile docker_build/kpi-backend
FROM python:3.12-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /srv/kpi

COPY pyproject.toml uv.lock /srv/kpi/

RUN uv sync --frozen --no-dev --no-install-project \
 && rm -rf /root/.cache/uv

ENV PATH="/srv/kpi/.venv/bin:$PATH" \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 1000:1000

ENTRYPOINT ["python", "-I", "-m", "uvicorn", "--app-dir", "/srv/kpi", "kpi_agent.backend.main:app", "--host", "0.0.0.0", "--port", "8099"]
