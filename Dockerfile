# Install the venv inside the runtime image (no COPY --from builder of .venv).
# BuildKit copying a torch/sentence-transformers venv fails on some CI hosts with:
# "open .../torch/lib/libtorch_global_deps.so: no such file or directory"
# (symlink/hardlink layout in PyTorch wheels vs CreateDiff/copy walker).

FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
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
    fonts-lohit-deva \
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
        https://github.com/Sandhi-IITBombay/Shobhika/releases/download/v1.05/Shobhika-1.05.zip \
    && mkdir -p /usr/share/fonts/truetype/shobhika \
    && unzip -j /tmp/shobhika.zip -d /usr/share/fonts/truetype/shobhika/ "*.otf" \
    && rm /tmp/shobhika.zip \
    && fc-cache -f -v

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8001

# Camoufox (scraping) + Playwright Chromium (HTML→PDF in html_pdf_translator).
# Browsers live under XDG_CACHE_HOME so appuser can read what root fetched at build.
ENV CAMOUFOX_CACHE_DIR=/app/.cache/camoufox \
    XDG_CACHE_HOME=/app/.cache \
    PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright
RUN mkdir -p /app/.cache/camoufox /app/.cache/ms-playwright && \
    /app/.venv/bin/playwright install --with-deps chromium && \
    /app/.venv/bin/python -m camoufox fetch && \
    chown -R appuser:appuser /app/.cache

USER appuser

# Matches deploy.yml -p HOST:8001 and knowlex-platform-api AGENT_SERVICE_URL (default :8001).
EXPOSE 8001

CMD sh -c "python -m uvicorn legal_agent.main:app --host 0.0.0.0 --port $PORT"
