FROM ubuntu:22.04

# ---- system deps: ffmpeg + VNC + window manager ----
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ffmpeg \
    xvfb x11vnc x11-utils \
    fluxbox \
    python3 python3-pip python3-tk \
    git curl wget \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

# ---- noVNC (web-based VNC client in browser) ----
RUN git clone --depth 1 https://github.com/novnc/noVNC.git /opt/novnc \
    && git clone --depth 1 https://github.com/novnc/websockify.git /opt/novnc/utils/websockify

# ---- Video-Fix-98 ----
WORKDIR /app
COPY requirements.txt . 2>/dev/null || true
RUN pip3 install --no-cache-dir pillow 2>/dev/null || true

COPY . .

# ---- entrypoint: Xvfb + fluxbox + x11vnc + noVNC + the app ----
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 6080
ENV DISPLAY=:99
ENV RESOLUTION=1024x768

ENTRYPOINT ["docker-entrypoint.sh"]
