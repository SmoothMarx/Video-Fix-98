#!/usr/bin/env bash
# =============================================================================
#  Video-Fix-98 — interactive installer / repairer
#
#  Modes:
#    ./setup.sh               interactive menu (install / repair / quit)
#    ./setup.sh install [dir] install the tool + dependencies
#    ./setup.sh repair  [dir] check a folder, auto-fix anything missing
#    ./setup.sh check   [dir] detect dependencies only (no changes)
#
#  Design (per user spec):
#    - Detects ALL existing dependencies first
#    - Asks where to install the tool (destination folder)
#    - Harnesses existing dependencies from their current locations when
#      present; installs missing ones into the destination/system as needed
#    - 'repair' checks a user-designated folder, re-runs the dependency check,
#      and fixes whatever is missing automatically (no report spam)
#    - Cross-platform aware: works on Linux/macOS and Git-Bash/Windows-ish
#      shells (paths adapt; Windows uses .exe suffix when available)
# =============================================================================

set -u

# ---- config ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXE=""; case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) EXE=".exe";; esac

TOOL_FILES=(salvage.py setup.sh README.md)
BIN_NAMES=(ffmpeg ffprobe untrunc)

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; }

# ---- helpers --------------------------------------------------------------
ask() { # ask <prompt> <default>
    local prompt="$1" default="$2" ans
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " ans
        printf '%s\n' "${ans:-$default}"
    else
        read -r -p "$prompt: " ans
        printf '%s\n' "$ans"
    fi
}

confirm() { # confirm <question> -> 0 yes / 1 no
    local ans
    read -r -p "$1 [y/N]: " ans
    case "${ans:-N}" in y|Y|yes|YES) return 0;; *) return 1;; esac
}

have() { command -v "$1" >/dev/null 2>&1; }

has_encoder() { # has_encoder <name>
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q " $1 "
}

has_filter() {
    ffmpeg -hide_banner -filters 2>/dev/null | grep -q "$1"
}

# ---- detection ------------------------------------------------------------
detect_deps() {
    # prints status lines; sets global flags: HAS_FFMPEG HAS_FFPROBE HAS_UNTRUNC
    HAS_FFMPEG=0; HAS_FFPROBE=0; HAS_UNTRUNC=0
    say "Detecting dependencies..."
    if have ffmpeg; then HAS_FFMPEG=1; ok "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"
    else warn "ffmpeg: MISSING"; fi
    if have ffprobe; then HAS_FFPROBE=1; ok "ffprobe: present"
    else warn "ffprobe: MISSING"; fi
    if [ "$HAS_FFMPEG" = 1 ]; then
        for enc in libx264 libx265 libvpx-vp9 libsvtav1; do
            if has_encoder "$enc"; then ok "  encoder $enc"
            else warn "  encoder $enc MISSING"; fi
        done
        if has_filter freezedetect; then ok "  filter freezedetect"
        else warn "  filter freezedetect MISSING"; fi
    fi
    if have untrunc; then HAS_UNTRUNC=1; ok "untrunc: $(untrunc -V 2>&1 | head -1 || echo present)"
    else warn "untrunc: MISSING (only needed for --fix untrunc)"; fi
}

local_untrunc() { # local_untrunc <destdir> -> prints path if present there
    local d="$1"
    if [ -x "$d/untrunc$EXE" ]; then printf '%s' "$d/untrunc$EXE"; fi
}

# ---- installs -------------------------------------------------------------
install_ffmpeg_system() {
    warn "ffmpeg not found — installing into the SYSTEM (default harness fails)."
    if ! confirm "Install ffmpeg via the system package manager?"; then
        warn "Skipped. The tool needs ffmpeg to work."
        return 1
    fi
    case "$(uname -s 2>/dev/null)" in
        Darwin) command -v brew >/dev/null 2>&1 && brew install ffmpeg || { err "Homebrew not found"; return 1; } ;;
        MINGW*|MSYS*|CYGWIN*) command -v winget >/dev/null 2>&1 && winget install ffmpeg || { err "winget not found"; return 1; } ;;
        *)
            if command -v apt-get >/dev/null 2>&1; then sudo apt-get update -qq && sudo apt-get install -y ffmpeg
            elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y ffmpeg
            elif command -v pacman >/dev/null 2>&1; then sudo pacman -Sy --noconfirm ffmpeg
            else err "no supported package manager (apt/dnf/pacman)"; return 1; fi ;;
    esac
}

install_untrunc_local() { # install_untrunc_local <destdir>
    local dest="$1" src
    say "Building untrunc into the destination folder..."
    if ! command -v git >/dev/null 2>&1; then err "git not found — cannot fetch untrunc"; return 1; fi
    if ! command -v make >/dev/null 2>&1; then err "make not found — cannot build untrunc"; return 1; fi
    # ensure ffmpeg dev headers exist (needed to compile)
    if [ "$(uname -s 2>/dev/null)" != "Darwin" ] && [ "$(uname -s 2>/dev/null | cut -c1-7)" != "MINGW" ]; then
        for pkg in libavformat-dev libavcodec-dev libavutil-dev; do
            if ! dpkg -s "$pkg" >/dev/null 2>&1; then
                warn "installing $pkg (needed to build untrunc)..."
                sudo apt-get install -y "$pkg" >/dev/null 2>&1 || sudo apt-get install -y \
                    git g++ make yasm pkg-config libavformat-dev libavcodec-dev libavutil-dev
                break
            fi
        done
    fi
    local bd; bd="$(mktemp -d)"
    if (cd "$bd" && git clone --depth 1 https://github.com/anthwlock/untrunc.git >/dev/null 2>&1 \
        && cd untrunc && make >/dev/null 2>&1); then
        cp "$bd/untrunc/untrunc$EXE" "$dest/untrunc$EXE" && chmod +x "$dest/untrunc$EXE"
        ok "untrunc built -> $dest/untrunc$EXE"
        rm -rf "$bd"
    else
        err "untrunc build failed (see output); --fix untrunc will be unavailable"
        rm -rf "$bd"
        return 1
    fi
}

# ---- install --------------------------------------------------------------
do_install() {
    local dest="${1:-}"
    detect_deps

    if [ -z "$dest" ]; then
        default_dest="$SCRIPT_DIR"
        dest="$(ask "Where should the tool be installed?" "$default_dest")"
    fi
    dest="${dest%/}"
    mkdir -p "$dest" || { err "cannot create $dest"; return 1; }
    say "Installing into: $dest"

    # 1. tool files
    for f in "${TOOL_FILES[@]}"; do
        if [ -f "$SCRIPT_DIR/$f" ]; then cp "$SCRIPT_DIR/$f" "$dest/$f"; ok "copied $f"
        else warn "$f missing from project dir — skipped"; fi
    done
    chmod +x "$dest/salvage.py" "$dest/setup.sh" 2>/dev/null

    # 2. ffmpeg: harness system (default), else install to system
    if [ "$HAS_FFMPEG" = 1 ]; then
        ok "harnessing system ffmpeg/ffprobe (no copy needed)"
    else
        install_ffmpeg_system || warn "ffmpeg unavailable — tool needs it"
    fi

    # 3. untrunc: harness if already installed; else build into destination
    if [ "$HAS_UNTRUNC" = 1 ]; then
        ok "harnessing system untrunc: $(command -v untrunc)"
    elif [ -x "$dest/untrunc$EXE" ]; then
        ok "untrunc already present in destination folder"
    else
        install_untrunc_local "$dest" || warn "untrunc not installed"
    fi

    say "Install complete."
    echo
    echo "  Tool:      $dest/salvage.py"
    echo "  Try it:    python3 $dest/salvage.py --help"
    echo "  Re-check:  $dest/setup.sh check"
    echo "  Repair:    $dest/setup.sh repair $dest"
}

# ---- repair ---------------------------------------------------------------
do_repair() {
    local target="${1:-}"
    if [ -z "$target" ]; then
        target="$(ask "Which folder should I check/repair?" "$SCRIPT_DIR")"
    fi
    target="${target%/}"
    if [ ! -d "$target" ]; then err "folder not found: $target"; return 1; fi
    say "Repairing installation in: $target"
    fixed=0

    # 1. tool files present?
    for f in "${TOOL_FILES[@]}"; do
        if [ -f "$target/$f" ]; then ok "$f present"
        elif [ -f "$SCRIPT_DIR/$f" ]; then
            cp "$SCRIPT_DIR/$f" "$target/$f"; chmod +x "$target/$f" 2>/dev/null
            ok "$f MISSING -> restored"; fixed=1
        else
            warn "$f missing and not available from project dir"
        fi
    done

    # 2. untrunc: present locally or on PATH?
    if have untrunc; then ok "untrunc on PATH"
    elif [ -x "$target/untrunc$EXE" ]; then ok "untrunc in folder"
    else
        say "untrunc missing — rebuilding into destination folder"
        install_untrunc_local "$target" && fixed=1
    fi

    # 3. ffmpeg/ffprobe present?
    detect_deps
    if [ "$HAS_FFMPEG" = 0 ]; then
        say "ffmpeg missing — installing into the system"
        install_ffmpeg_system && fixed=1
    fi

    if [ "$fixed" = 0 ]; then ok "everything looks correct — no repair needed"
    else ok "repair complete"; fi
    echo
    echo "  Verify:    python3 $target/salvage.py --help"
}

# ---- check (detect only) --------------------------------------------------
do_check() {
    detect_deps
    echo
    echo "  Quick summary:"
    echo "    ffmpeg:   $([ "$HAS_FFMPEG" = 1 ] && echo OK || echo MISSING)"
    echo "    ffprobe:  $([ "$HAS_FFPROBE" = 1 ] && echo OK || echo MISSING)"
    echo "    untrunc:  $([ "$HAS_UNTRUNC" = 1 ] && echo OK || echo MISSING)"
    echo "    encoders: $(for e in libx264 libx265 libvpx-vp9 libsvtav1; do has_encoder "$e" && echo -n "$e "; done)"
}

# ---- menu -----------------------------------------------------------------
menu() {
    echo
    say "Video-Fix-98 setup"
    echo "  1) Install    — detect deps, choose destination, install tool + deps"
    echo "  2) Repair     — check a folder, auto-fix missing files/deps"
    echo "  3) Check      — detect dependencies only (no changes)"
    echo "  4) Quit"
    local ans
    read -r -p "Choose [1-4]: " ans
    case "$ans" in
        1) do_install ;;
        2) do_repair ;;
        3) do_check ;;
        *) echo "bye"; exit 0 ;;
    esac
}

# ---- entry ----------------------------------------------------------------
MODE="${1:-menu}"
case "$MODE" in
    install) do_install "${2:-}" ;;
    repair)  do_repair "${2:-}" ;;
    check)   do_check ;;
    menu|"") menu ;;
    *) echo "usage: $0 [install|repair|check|menu]"; exit 1 ;;
esac
