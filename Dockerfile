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

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# Copy venv and source
COPY --from=builder /app/.venv /app/.venv
COPY src/ src/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install Playwright chromium and its system dependencies
RUN /app/.venv/bin/playwright install chromium --with-deps

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn legal_agent.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
