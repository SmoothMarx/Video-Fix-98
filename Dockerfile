# Salvage — self-contained installer container
#
# Build:   docker build -t salvage .
# Run:     docker run --rm -v /path/to/media:/data:ro -v /path/to/out:/out salvage \
#            /data/broken.mp4 --mode repair --out-dir /out
#
# The image bundles Python, ffmpeg/ffprobe (with all encoders), and untrunc
# (built from source) — everything the tool needs, no host installs.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# 1. base deps + ffmpeg (Ubuntu 24.04 ships 6.1.1 with libx264/libx265/
#    libvpx-vp9/libsvtav1 and the freezedetect filter)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        python3 \
        ca-certificates \
        git \
        g++ \
        make \
        yasm \
        pkg-config \
        libavformat-dev \
        libavcodec-dev \
        libavutil-dev \
        libavfilter-dev \
        libswscale-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. build untrunc from source (GPL-2.0, anthwlock fork)
WORKDIR /build
RUN git clone --depth 1 https://github.com/anthwlock/untrunc.git \
    && cd untrunc && make >/dev/null \
    && cp untrunc /usr/local/bin/untrunc \
    && cd / && rm -rf /build

# 3. copy the tool
WORKDIR /app
COPY salvage.py setup.sh README.md /app/
RUN chmod +x /app/setup.sh

# 4. entrypoint: salvage.py with all args passed through
ENTRYPOINT ["python3", "/app/salvage.py"]
CMD ["--help"]
