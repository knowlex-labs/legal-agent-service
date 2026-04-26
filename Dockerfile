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
    tesseract-ocr-hin \
    tesseract-ocr-tam \
    tesseract-ocr-ben \
    poppler-utils \
    fonts-noto \
    fonts-noto-cjk \
    fonts-noto-extra \
    fonts-noto-color-emoji \
    fonts-liberation \
    fonts-sahadeva \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libcairo2 \
    libffi-dev \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Shobhika (Devanagari serif used in Indian legal documents).
# Not packaged in apt, so fetch the upstream release directly.
RUN curl -fsSL -o /tmp/shobhika.zip \
        https://github.com/Sandhi-IITBombay/Shobhika/releases/download/v1.04/Shobhika-v1.04.zip \
    && mkdir -p /usr/share/fonts/truetype/shobhika \
    && unzip -j /tmp/shobhika.zip -d /usr/share/fonts/truetype/shobhika/ "*.otf" "*.ttf" \
    && rm /tmp/shobhika.zip \
    && fc-cache -f -v

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
