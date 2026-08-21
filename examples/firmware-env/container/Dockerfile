FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc \
      libc6-dev \
      make \
      curl \
      ca-certificates \
      python3 \
      python3-yaml \
      git \
    && rm -rf /var/lib/apt/lists/*

RUN export UV_INSTALL_DIR=/usr/local/bin; \
    curl -LsSf https://astral.sh/uv/0.12.3/install.sh | sh

# picked up by every interactive bash shell (e.g. a devcontainer terminal
# VS Code starts fresh) once `denver run --export-env /tmp/denver.env` has
# written it; a no-op until then. At build time (root) so no runtime sudo
# is needed, and /etc/bash.bashrc rather than ~/.bashrc since $HOME is a
# runtime bind mount (create-env.sh's container-home folder) that would
# shadow anything written to ~/.bashrc at image-build time.
RUN ( \
        echo 'echo "Checking for /tmp/denver.env ..."'; \
        echo '[ -f /tmp/denver.env ] && . /tmp/denver.env'; \
    ) >> /etc/bash.bashrc
