FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        curl \
        gcc \
        libpq-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY agent_gateway ./agent_gateway

RUN pip install --upgrade pip \
    && pip install -e .

COPY config ./config
COPY workspace ./workspace

EXPOSE 8765 8766 8780

CMD ["agent-gateway", "serve"]
