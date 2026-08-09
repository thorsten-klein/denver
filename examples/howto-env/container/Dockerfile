FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      jq \
      net-tools \
      curl \
      ca-certificates \
      python3 \
      python3-yaml \
      git \
    && rm -rf /var/lib/apt/lists/*

RUN export UV_INSTALL_DIR=/usr/local/bin; \
    curl -LsSf https://astral.sh/uv/0.12.3/install.sh | sh
