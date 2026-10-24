# Terminal-only Raspberry Pi OS-like environment (Debian arm64 + Raspberry Pi apt repo)
# Build with:  docker build --platform linux/arm64 -t pi-env .
FROM debian:trixie

ARG CODENAME=trixie
ENV DEBIAN_FRONTEND=noninteractive

# Install Raspberry Pi's official keyring package (the old raspberrypi.gpg.key
# uses a SHA-1 binding that Debian Trixie's apt now rejects).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl sudo; \
    POOL=https://archive.raspberrypi.com/debian/pool/main/r/raspberrypi-archive-keyring/; \
    DEB=$(curl -fsSL "$POOL" | grep -o 'raspberrypi-archive-keyring_[^"<>]*_all\.deb' | sort -uV | tail -n1); \
    echo "Using $DEB"; \
    curl -fsSL "$POOL$DEB" -o /tmp/rpi-keyring.deb; \
    dpkg -i /tmp/rpi-keyring.deb; \
    rm /tmp/rpi-keyring.deb; \
    KEY=$(ls /usr/share/keyrings/raspberrypi-archive-keyring.* | head -n1); \
    echo "deb [signed-by=$KEY] http://archive.raspberrypi.com/debian/ ${CODENAME} main" \
        > /etc/apt/sources.list.d/raspi.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git nano less iputils-ping openssh-client; \
    rm -rf /var/lib/apt/lists/*

# Default Pi-style user with passwordless sudo
RUN useradd -m -s /bin/bash pi \
    && echo "pi ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/pi

USER pi
WORKDIR /home/pi
CMD ["/bin/bash"]