FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_PROGRESS_BAR=off

WORKDIR /app

RUN printf '%s\n' \
    'Types: deb' \
    'URIs: https://mirrors.tuna.tsinghua.edu.cn/debian/' \
    'Suites: trixie trixie-updates' \
    'Components: main contrib non-free non-free-firmware' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    '' \
    'Types: deb' \
    'URIs: https://mirrors.tuna.tsinghua.edu.cn/debian-security/' \
    'Suites: trixie-security' \
    'Components: main contrib non-free non-free-firmware' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    > /etc/apt/sources.list.d/debian.sources

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        curl \
        fonts-noto-cjk \
        gcc \
        librsvg2-bin \
        libpq-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=20

COPY pyproject.toml README.md ./
COPY agent_gateway ./agent_gateway
COPY scripts ./scripts

RUN pip install --no-input -i https://pypi.tuna.tsinghua.edu.cn/simple --default-timeout 600 --retries 20 -e .

COPY config ./config
COPY workspace ./workspace

EXPOSE 8765 8766 8780

CMD ["agent-gateway", "serve"]
