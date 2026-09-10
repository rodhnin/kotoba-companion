# Kotoba backend. The build context is the repo root so the image gets soul/ too: the server reads
# her personality file on startup and crashes without it.
#
# 3.14 is this image's choice, not the project's floor — self-hosting on 3.11 is supported.
FROM python:3.14-slim

WORKDIR /app

# Kept light — code and shell run in the sandbox, not here. npm is for the stdio MCP servers, chromium
# only renders reports to PDF. The browser MCP is deliberately absent: it drives the USER'S browser
# over CDP, so its own chromium would never run.
RUN apt-get update && apt-get install -y --no-install-recommends \
      nodejs npm chromium fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, from the manifest alone, so a code change does not invalidate the layer.
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY api/pyproject.toml api/kotoba_build.py api/_web_check.py api/README.md* ./api/
RUN mkdir -p api/src/kotoba && printf '__version__ = "0.0.0"\n' > api/src/kotoba/__init__.py \
    && pip install --no-cache-dir -e "./api[server,voice,mcp,web]" && rm -rf api/src

# App code + the personality file the loader seeds from. The package is already installed in editable
# mode, so this COPY is what makes `kotoba.server:app` resolve.
COPY api ./api
COPY soul ./soul

# Run as a non-root user. Both mount points must EXIST here and be owned by them: Docker copies a
# directory's ownership into a fresh volume, but invents a root-owned one when it is missing.
#   /app/data             the database and the MCP config
#   /home/kotoba/.kotoba  the workspace: files, memory, settings, keystore, Live2D models — which
#                         belong on a volume, since an image cannot be written to.
RUN useradd --create-home --uid 10001 kotoba \
    && mkdir -p /app/data /home/kotoba/.kotoba \
    && chown -R kotoba:kotoba /app /home/kotoba
USER kotoba

WORKDIR /app/api

# Absolute so it resolves whatever the working dir; the database defaults container-local so a bare
# `docker run` works, and compose points it at the volume instead of the container layer.
ENV SOUL_PATH=/app/soul/default.md
ENV DATABASE_URL=sqlite:///./kotoba.db
ENV PYTHONUNBUFFERED=1
# An unprivileged uid without user namespaces cannot build a Chromium sandbox: it exits saying so, and
# reports render as nothing. Gating this on root alone was wrong for exactly this container.
ENV KOTOBA_CHROMIUM_NO_SANDBOX=1
# Installed MCP servers live on the volume so a redeploy does not lose them.
ENV KOTOBA_MCP_CONFIG=/app/data/mcp.yaml

EXPOSE 8000

# A host that assigns its own port sets $PORT; a plain `docker run` gets 8000.
CMD ["sh", "-c", "uvicorn kotoba.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
