#!/bin/bash
# setup.sh — make the salvage project self-contained.
# Detects missing dependencies FIRST, then installs only what's needed:
#   - ffmpeg + ffprobe   (system package; provides all encoders + filters)
#   - untrunc            (built from source; GPL-2.0, anthwlock fork)
#
# License note: ffmpeg (LGPL/GPL build) and untrunc (GPL-2.0) are both
# free to install and use. This script only installs what's missing.
set -e

BIN_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/salvage_build"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }

missing=0

# ---------- 1. detect ----------
say "Detecting dependencies..."

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    ok "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"
    # verify required encoders/filters exist
    for enc in libx264 libx265 libvpx-vp9 libsvtav1; do
        if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q " $enc "; then
            ok "  encoder: $enc"
        else
            warn "  encoder $enc missing — reinstall ffmpeg with full codecs"
            missing=1
        fi
    done
    if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q freezedetect; then
        warn "  freezedetect filter missing — ffmpeg too old or stripped"
        missing=1
    fi
else
    warn "ffmpeg/ffprobe not found"
    missing=1
fi

if command -v untrunc >/dev/null 2>&1; then
    ok "untrunc: $(untrunc -V 2>&1 | head -1 || echo present)"
else
    warn "untrunc not found"
    missing=1
fi

# ---------- 2. install only what's missing ----------
if [ "$missing" -eq 0 ]; then
    say "All dependencies present. Nothing to install."
    exit 0
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    say "Installing ffmpeg (system package)..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq
        sudo apt-get install -y ffmpeg
    else
        echo "ERROR: no apt-get found. Install ffmpeg manually, then re-run."
        exit 1
    fi
fi

# build untrunc from source (needs ffmpeg dev headers + build tools)
if ! command -v untrunc >/dev/null 2>&1; then
    say "Building untrunc from source (GPL-2.0, anthwlock fork)..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y git g++ make yasm pkg-config \
            libavformat-dev libavcodec-dev libavutil-dev \
            libavfilter-dev libswscale-dev >/dev/null 2>&1 || \
            sudo apt-get install -y git g++ make yasm pkg-config \
            libavformat-dev libavcodec-dev libavutil-dev
    fi
    mkdir -p "$BUILD_DIR"
    cd "$BUILD_DIR"
    [ -d untrunc ] || git clone --depth 1 https://github.com/anthwlock/untrunc.git
    cd untrunc
    make >/dev/null
    sudo cp untrunc /usr/local/bin/untrunc
    ok "untrunc installed: $(command -v untrunc)"
fi

say "Done. Re-run: python3 salvage.py --help"
