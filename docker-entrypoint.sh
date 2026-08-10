#!/usr/bin/env bash
set -e

# Start virtual display
Xvfb :99 -screen 0 "${RESOLUTION:-1024x768}x24" &
sleep 1

# Window manager (needed for proper window decorations)
fluxbox &
sleep 1

# Start x11vnc (no password — LAN/internal use only)
x11vnc -display :99 -forever -nopw -quiet -listen 0.0.0.0 -xkb &
sleep 1

# Start noVNC web server on port 6080
/opt/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 &
sleep 1

echo "=========================================="
echo "  Video-Fix-98 web interface"
echo "  Open: http://localhost:6080/vnc.html"
echo "=========================================="

# Launch the GUI or CLI depending on argument
if [ "${1:-gui}" = "cli" ]; then
    exec python3 /app/salvage.py "${@:2}"
else
    exec python3 /app/gui.py
fi
