# syntax=docker/dockerfile:1.7
# Images:  --target web     API + built frontend (no TeX, no CLIs, no Docker socket)
#          --target worker  compile + agent worker: TeX Live, bubblewrap, git, node CLIs
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm
ARG NODE_IMAGE=node:24.11.1-bookworm-slim

FROM ${NODE_IMAGE} AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 prism && useradd -u 10001 -g prism -m -d /home/prism prism
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY sample/ /app/sample/
ENV PATH=/app/backend/.venv/bin:$PATH PRISM_DATA_DIR=/srv/prism/data
RUN mkdir -p /srv/prism/data && chown -R prism:prism /srv/prism

FROM base AS web
COPY --from=frontend /src/frontend/dist /app/frontend/dist
ENV PRISM_FRONTEND_DIST=/app/frontend/dist
USER prism
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]

FROM base AS worker
# TeX Live from Debian (2022). Add packages here if your papers need them.
RUN apt-get update && apt-get install -y --no-install-recommends \
      latexmk texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra \
      texlive-bibtex-extra texlive-science texlive-xetex texlive-luatex biber bubblewrap \
    && rm -rf /var/lib/apt/lists/*
# Node runtime for the agent CLIs (Codex, Claude Code; Antigravity per vendor instructions).
COPY --from=node:24.11.1-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24.11.1-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ARG INSTALL_CODEX=true
ARG CODEX_VERSION=0.165.0
RUN if [ "$INSTALL_CODEX" = "true" ]; then npm install -g @openai/codex@${CODEX_VERSION}; fi
ARG INSTALL_CLAUDE=true
ARG CLAUDE_CODE_VERSION=2.1.312
RUN if [ "$INSTALL_CLAUDE" = "true" ]; then npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}; fi
# Antigravity CLI: install according to https://antigravity.google/docs (not bundled here).
ENV TEX_BIN_DIR=/usr/bin TOOL_SANDBOX=bwrap COMPILE_SANDBOX=bwrap
USER prism
CMD ["python", "-m", "app.worker"]
