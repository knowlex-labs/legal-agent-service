# ---- builder stage ----
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies (skip the local package — no README.md needed)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# ---- runtime stage ----
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# Copy venv and source
COPY --from=builder /app/.venv /app/.venv
COPY src/ src/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install Camoufox browser (Firefox-based anti-detect) and Playwright deps
RUN /app/.venv/bin/python -m camoufox fetch && \
    /app/.venv/bin/playwright install-deps

USER appuser

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "legal_agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
