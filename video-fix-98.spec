# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Video-Fix-98.exe — the single self-contained GUI app.

ONE EXE. Everything the app needs lives inside:
  - gui.py itself
  - salvage.exe (the CLI engine, which itself bundles ffmpeg/ffprobe/untrunc)
  - assets (icon.png, logo.png, icon.ico) for window icon, splash, logo
  - tkinter (auto-included)

Build (on Windows / Wine):
    pyinstaller --clean -y --dist ./dist/windows --workpath %TEMP% video-fix-98.spec
"""
import os
import glob

SPEC_DIR = os.path.dirname(SPEC)

# Locate the CLI engine exe — prefer the fully-bundled build (dist/windows),
# then the plain dist copy.
candidates = [
    os.path.join(SPEC_DIR, "dist", "windows", "salvage.exe"),
    os.path.join(SPEC_DIR, "dist", "salvage.exe"),
]
cli_exe = next((p for p in candidates if os.path.exists(p)), None)
if cli_exe is None:
    raise SystemExit("ERROR: salvage.exe not found — build salvage.spec first")

datas = [
    ("assets/icon.png", "assets"),
    ("assets/logo.png", "assets"),
    (cli_exe, "."),          # engine exe goes into the bundle root
]

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox'],
    hookspath=[],
    runtime_hooks=[],
    excludes=["test", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Video-Fix-98",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # GUI: no console window
    icon="assets/icon.ico",
)
