# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for salvage.exe — the CLI tool.

Bundles salvage.py plus the native tools (ffmpeg, ffprobe, untrunc) so the
exe is fully self-contained. The salvage.py bootstrap puts sys._MEIPASS on
PATH so the bundled tools are found at runtime.

Build (on Windows):
    pyinstaller --clean salvage.spec
"""
import os

BIN = os.path.join(os.path.dirname(SPEC), "bin", "win")  # where Windows binaries live

binaries = []
for tool in ("ffmpeg.exe", "ffprobe.exe", "untrunc.exe"):
    p = os.path.join(BIN, tool)
    if os.path.exists(p):
        binaries.append((p, "."))

a = Analysis(
    ["salvage.py"],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="salvage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # CLI: keep the console window
    icon="assets/icon.ico",
)
