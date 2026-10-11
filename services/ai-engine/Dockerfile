# FactoryMind AI engine — container image (spec §19, §22).
#
# Build context is this directory:
#   docker build -t factorymind-ai-engine ./services/ai-engine
#
# The image carries only the service: no test tooling, no benchmark scripts, no
# `.venv`. Runtime configuration arrives as environment variables at run time,
# never baked in (spec §31) — the same rule the service already follows locally.
FROM python:3.11-slim

# Package downloads go through a mirror by default. Measured from the target
# network, the default PyPI index ran at ~40 kB/s — a 20-minute build. Override
# for other environments:
#   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple ...
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so the layer is reused while only source changes.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt

# The service code and the tunable rule files. Config lives beside the code
# because `app.config` resolves it relative to the package root.
COPY app ./app
COPY config ./config

# Drop privileges: the service only reads config and talks outbound, so root
# buys nothing and widens the blast radius of a compromised dependency.
RUN useradd --create-home --uid 10001 factorymind
USER factorymind

EXPOSE 8100
ENV AI_ENGINE_HOST=0.0.0.0 \
    AI_ENGINE_PORT=8100

# Liveness against `/healthz`, which deliberately touches no provider: a slow
# supOS gateway must surface as a data-source fault, not as a restarted pod.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "app"]