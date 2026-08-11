FROM ubuntu:22.04

RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ffmpeg \
    xvfb x11vnc x11-utils \
    fluxbox \
    python3 python3-pip python3-tk \
    git curl \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/novnc/noVNC.git /opt/novnc \
    && git clone --depth 1 https://github.com/novnc/websockify.git /opt/novnc/utils/websockify

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 6080
ENV DISPLAY=:99
ENV RESOLUTION=1024x768

ENTRYPOINT ["docker-entrypoint.sh"]
