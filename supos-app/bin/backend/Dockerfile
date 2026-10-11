# FactoryMind AI engine — image for the open supOS app package.
#
# Build context is this directory (`bin/backend`), which the packaging script
# (`scripts/package-app.ps1`) stages from `services/ai-engine`. Keep this in
# sync with the dev-time image `services/ai-engine/Dockerfile`; the two differ
# only in build context, not in steps.
#
#   docker build -t factorymind-ai-backend:1.0.0 bin/backend
#
# Runtime configuration arrives as environment variables at deploy time, never
# baked in (spec §31).
FROM python:3.11-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt

COPY app ./app
COPY config ./config

RUN useradd --create-home --uid 10001 factorymind
USER factorymind

EXPOSE 8100
ENV AI_ENGINE_HOST=0.0.0.0 \
    AI_ENGINE_PORT=8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "app"]
