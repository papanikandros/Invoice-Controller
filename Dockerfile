# Invoice-Controller web UI — deployment image (todo #8, 2026-09-16).
#
# System deps beyond Python: tesseract + deu (local OCR tier keeps scans on-prem),
# poppler-utils (pdftotext for location-description). Secrets are NEVER baked in —
# provide .env at runtime (docker compose `env_file`).

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-deu poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first (cache-friendly): lockfile-exact, with the ocr extra,
# without dev tooling.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra ocr --no-dev --no-install-project

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --extra ocr --no-dev

# Run history lives here — mount a volume to survive container replacement.
RUN mkdir -p /app/tmp/webruns

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://127.0.0.1:8080/ >/dev/null || exit 1

# Binds to all interfaces INSIDE the container network only — docker-compose does
# not publish this port on the host; Caddy is the sole entrance.
CMD ["invoice-controller", "serve", "--host", "0.0.0.0", "--port", "8080"]
