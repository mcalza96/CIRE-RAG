# ------------------------------------------------------------------------------------
# BASE
# ------------------------------------------------------------------------------------
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-core.txt /app/requirements-core.txt

# ------------------------------------------------------------------------------------
# API IMAGE
# ------------------------------------------------------------------------------------
FROM base AS api_image

ENV RUNNING_IN_DOCKER="true" \
    JINA_MODE="CLOUD"

RUN pip install -r /app/requirements-core.txt

COPY . /app

RUN chmod +x /app/start_api.sh /app/start_worker.sh

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["./start_api.sh"]

# ------------------------------------------------------------------------------------
# WORKER CLOUD IMAGE (default for Railway/Render)
# ------------------------------------------------------------------------------------
FROM api_image AS worker_cloud_image

CMD ["./start_worker.sh"]

# ------------------------------------------------------------------------------------
# WORKER IMAGE (local/heavy runtime)
# ------------------------------------------------------------------------------------
FROM base AS worker_image

ENV RUNNING_IN_DOCKER="true" \
    JINA_MODE="LOCAL"

COPY requirements-local.txt /app/requirements-local.txt

RUN pip install -r /app/requirements-core.txt && \
    if [ -s /app/requirements-local.txt ]; then pip install -r /app/requirements-local.txt; fi

COPY . /app

RUN chmod +x /app/start_api.sh /app/start_worker.sh

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

CMD ["./start_worker.sh"]
