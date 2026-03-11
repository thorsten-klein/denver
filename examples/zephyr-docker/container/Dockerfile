FROM ubuntu:24.04

USER root

# ------------------------------------------------
LABEL info="Basic environment variables"
# ------------------------------------------------

ENV DEBIAN_FRONTEND=noninteractive

# Set the proper environment.
ENV LANG="en_US.UTF-8"
ENV LANGUAGE="en_US:en"
ENV LC_ALL="en_US.UTF-8"


# ------------------------------------------------
LABEL info="Add apt_update_install_clean.sh"
# ------------------------------------------------
ADD apt_update_install_clean.sh /usr/bin/

# ------------------------------------------------
LABEL info="Generate locale"
# ------------------------------------------------

RUN apt_update_install_clean.sh  \
        sudo \
        locales \
        language-pack-en \
        apt-utils \
        apt-transport-https \
    && locale-gen en_US.UTF-8 \
    && update-locale LANG=en_US.UTF-8 LC_MESSAGES=POSIX

# ------------------------------------------------
LABEL info="Setup default user (ubuntu) with password 'docker'"
# ------------------------------------------------

RUN echo "ubuntu:docker" | chpasswd && \
    usermod -a -G sudo ubuntu && \
    usermod -a -G dialout ubuntu && \
    usermod -a -G plugdev ubuntu && \
    usermod -a -G tty ubuntu && \
    echo 'ubuntu ALL=(ALL) ALL' >> /etc/sudoers.d/ubuntu && \
    echo 'Defaults env_keep += "http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY"' >> /etc/sudoers.d/ubuntu

RUN echo "ubuntu:docker" | chpasswd

RUN echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/bin/apt-get' >> /etc/sudoers.d/ubuntu && \
    echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/bin/patch' >> /etc/sudoers.d/ubuntu && \
    echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/bin/bash' >> /etc/sudoers.d/ubuntu && \
    echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/bin/mkdir' >> /etc/sudoers.d/ubuntu && \
    echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/bin/cp' >> /etc/sudoers.d/ubuntu && \
    echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/sbin/chroot' >> /etc/sudoers.d/ubuntu

# ------------------------------------------------
LABEL info="Initialize docker container to work with zephyr"
# ------------------------------------------------

ADD init_system_for_zephyr.sh /

RUN /init_system_for_zephyr.sh

# ------------------------------------------------
LABEL info="Setup python packages into docker"
# ------------------------------------------------

ADD requirements.txt /

RUN pip3 install -r /requirements.txt --break-system



# ------------------------------------------------
LABEL info="Setup fixuid"
# ------------------------------------------------
# workaround from https://github.com/boxboat/fixuid

ADD fixuid /home/ubuntu/fixuid

# ------------------------------------------------
LABEL info="Change user to 'ubuntu'"
# ------------------------------------------------

RUN USER=ubuntu && \
    GROUP=ubuntu && \
    ( cd /home/ubuntu/fixuid && curl -LO https://github.com/boxboat/fixuid/releases/download/v0.6.0/fixuid-0.6.0-linux-amd64.tar.gz ) && \
    cat /home/ubuntu/fixuid/fixuid*.tar.gz | tar -C /usr/local/bin -xzf - && \
    chown root:root /usr/local/bin/fixuid && \
    chmod 4755 /usr/local/bin/fixuid && \
    mkdir -p /etc/fixuid && \
    cat /home/ubuntu/fixuid/fixuid-config.yml > /etc/fixuid/config.yml && \
    rm -rf /home/ubuntu/fixuid

# --------------------------------------------------------------
# default command
# --------------------------------------------------------------
CMD ["sleep", "infinity"]

USER ubuntu:ubuntu

# ------------------------------------------------
LABEL info="Create directories for user configs as user (for correct permissions)"
# ------------------------------------------------

RUN mkdir -p \
        $HOME/.config/fish \
        $HOME/.local/share \
        $HOME/.cache \
        $HOME/.java

ENTRYPOINT ["fixuid", "-q"]
