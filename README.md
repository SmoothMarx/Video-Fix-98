# <img src="assets/icon.png" width="28">  Video-Fix-98 — Corrupt Video Checker & Repair Tool

<p align="center">
  <img src="assets/logo.png" width="340" alt="Video-Fix-98 logo">
</p>

**Video-Fix-98** is a self-contained tool that rescues video files damaged by
corruption, truncation, or broken index structures. It reads the file the way a
player would — frame by frame, beginning to end — finds the stretches that are
frozen or broken, and rebuilds a clean, continuous, playable video containing
**only the genuine motion frames**.

No AI. No cloud. Pure Python calling ffmpeg and (optionally)
untrunc, both installed natively on your machine.

---

## ✨ What it does

| Capability | Description |
|---|---|
| 🖥️ **GUI** | Desktop app (tkinter) with drag-resize panes, progress bars, three-tab notebook, session save/restore |
| 🔍 **Check** | Assess any video and report the damage: how much real content is left, size, duration, resolution, audio, error type, and what's recoverable |
| ⚡ **Quick check** | Healthy files detected in ~5s (skips the full freezedetect pass) |
| 🔧 **Repair** | Fix it — automatically choosing the best method for the damage found |
| 🧠 **Auto mode** | Picks the right fix: moov missing → untrunc, frozen frames → salvage, container damage → remux |
| 📋 **Reports** | Console report + CSV export + in-GUI Report/Results tabs with filtering |
| 🎛️ **Interactive** | Guided walkthrough: explains every option, asks for your values |
| 📦 **Batch** | Point at a folder and it processes every video in it (recursive with `--recursive`) |
| 🎵 **Audio salvage** | Partial audio recovery — trims audio to match salvaged video segments |
| ⏸️ **Pause/Stop** | Pause between files, stop mid-process with partial results shown |
| 💾 **Session save** | Save full session (sources, check results, repair data, checked state) as JSON; import to resume later |
| 🛡️ **Safety** | Never overwrites your original — auto-appends `_salvaged`, collision-safe `(1)`, `(2)` naming |

---

## 🚀 Quick Start

### Windows (recommended)
Download the self-contained exe from [releases](https://github.com/SmoothMarx/Video-Fix-98/releases):
- `Video-Fix-98.exe` — GUI (double-click)
- `salvage.exe` — CLI

### Linux
Download the `v1.3.0-linux` release binaries, or run from source:
```bash
./setup.sh
python3 salvage.py broken.mp4 --mode repair
python3 gui.py              # GUI (requires tkinter)
```

### Docker (web GUI)
```bash
docker compose up -d --build
# Open http://localhost:6080/vnc.html
```
The GUI runs in a browser via noVNC. Drop files in `input/`, repaired files land in `output/`.

---

## 🧠 The story: why would you even need this?

You're digging through an old hard drive. A backup from 2015. There's a folder
called "Family Videos" with 87 files — some play fine, some stutter and freeze
after 15 seconds, some won't open at all. You know there's real footage in
there. Birthday parties. Your kid's first steps. You just can't watch it.

Or maybe it's a Linux ISO you were torrenting last week. The download got to
64% before the seeder vanished. Your torrent client says it's "complete" but
VLC freezes on frame 3. The file is 800MB of data — maybe 500 of it is good.
You don't want to re-download it. You just want the good 500MB.

Or maybe someone sent you a video on WhatsApp that "arrived" at 2.3MB instead
of 23MB. It opens — sort of. The first 8 seconds play, then it freezes, but the
audio keeps going. The rest of the clip is there, just... stuck.

**These are all the same problem:** the file has real content buried inside
corruption. The decoder freezes on the last good frame (error concealment) and
you lose everything after.

**Video-Fix-98 is the corrupt detective.** You point it at a folder — any
folder. It walks through every subfolder, finds every video file, and checks
each one individually. It tells you exactly what's wrong: "this one has a
missing index, this one has frozen frames from 12:03 to 18:47, this one is
healthy." You pick which ones to repair, and it trims off the fat — extracting
only the decodable frames onto a clean, continuous timeline.

The result is smaller, smooth, and **every frame in it is real content.** No AI.
No guessing. Just frame-by-frame forensic analysis.

You keep the memories. You drop the corruption.

## 🔧 Run modes

| Mode | What it does |
|---|---|
| `--mode check` *(default)* | Read-only assessment. Reports size, resolution, codecs, claimed vs real duration, frozen stretches, estimated repair outcome. |
| `--mode repair` | Runs the check, then repairs. Final report shows **actual** output size/duration. |

---

## 🔧 Repair methods (`--fix`)

| Fix | What it does |
|---|---|
| `--fix auto` *(default)* | **Decides the best method automatically** from the damage found |
| `--fix salvage` | Trim frozen stretches, re-encode real motion frames into a continuous video |
| `--fix remux` | Lossless container rebuild (`-c copy`, metadata preserved). Falls back to salvage if the index lies. |
| `--fix untrunc` | Rebuilds a missing/corrupt `moov` index using a healthy reference file |
| `--fix none` | No fix — report only |

---

## 🎛️ Options

| Argument | Default | What it controls |
|---|---|---|
| `input` | *(required)* | Source **file** or **folder** (folder = batch) |
| `--mode MODE` | `check` | `check` = assess only; `repair` = assess + fix |
| `--fix FIX` | `auto` | Repair method |
| `--reference FILE` | *(auto)* | Healthy reference for `--fix untrunc` |
| `--out-dir DIR` | beside input / `Video Fixer Output` | Output location |
| `--container NAME` | `mkv` | Output container: mkv, mp4, mov, avi, ts, webm, m4v, flv |
| `--codec NAME` | `h264` | Video codec: h264, hevc, vp9, av1 |
| `--audio-mode MODE` | `copy` | Audio: off (drop), copy (keep if decodable), aac (re-encode 128k) |
| `--min-freeze N` | `2.0` | Minimum frozen stretch (seconds) to trim |
| `--noise VALUE` | `-60dB` | Pixel-difference threshold for "same frame" |
| `--margin N` | `1.0` | Safety buffer (seconds) around each frozen zone |
| `--crf N` | `20` | Encode quality — **lower = better quality but bigger** |
| `--preset NAME` | `veryfast` | Encode speed/size: ultrafast → veryfast → medium → slow |
| `--fps N` | `50` | Output frame rate |
| `--recursive` | off | When input is a folder, scan subdirectories recursively |
| `--no-quick` | off | Disable quick check for healthy files (force full freezedetect) |
| `--force-pass` | off | Run full frame pass even on sparse files |
| `--interactive` | off | Walk through every option step by step |
| `--report PATH` | *(prompt)* | Write CSV report without prompting |
| `--gui` | off | Emit VF98PCT progress lines for the desktop GUI |
| `-h, --help` | — | Show full usage |

---

## 🖥️ GUI features

- **Three-tab notebook:** Progress Log | Report | Results
- **Sidebars:** Drag-resize source queue and output folder panes
- **Check button:** Scans all source files, populates Report tab with per-file results
- **Quick skip:** Healthy files check in ~5s, corrupt files get full freezedetect
- **Checkmarks:** ✓ prefix in source list for successfully checked files
- **Filter:** Report tab dropdown filters by All / Corrupt only / Healthy only
- **Uncheck healthy by default:** Only corrupt files are auto-ticked for repair
- **Progress bar:** Green native bar during both Check and Repair phases
- **Phase labels:** Status bar shows "encoding 67%" / "muxing" during repair
- **Partial audio salvage:** Audio trimmed to match salvaged video segments
- **Pause:** Waits for current file, then pauses before next
- **Stop:** Immediately terminates, partial results shown
- **Session save/restore:** JSON stores sources, check results, repair data, checked state
- **Import:** Resumes from last unchecked file — doesn't re-check completed files
- **Sub-folders:** Checkbox for recursive folder scanning
- **Output same as source:** Checkbox to save repaired files alongside originals

---

## 📋 Reports

After **any** run, a report is printed to the console. The tool then asks whether
to save it as CSV. For automation, pass `--report <path>`.

CSV columns: `file, size_bytes, alloc_pct, resolution, video_codec, fps,
audio_codec, claimed_duration, good_seconds, decodable_pct,
estimated_size_bytes, error, final_size_bytes, final_duration,
final_frozen_seconds, verdict` (final columns filled by repair mode only).

---

## 🛡️ Safety

- **Your original is never modified.** Output always goes to a new file.
- **Filenames:** Auto-suffixed `_salvaged` before extension; if a name conflicts, Windows-style `(1)`, `(2)` numbering is used.
- **Sparse files** (never downloaded, only pre-allocated) are detected and skipped unless `--force-pass` is set.
- **Quick check** skips the expensive freezedetect pass on apparently healthy files (no error signature, >95% allocated, duration >0).

---

## 📦 Project layout

```
video-salvage/
├── salvage.py              # CLI tool (Python stdlib)
├── gui.py                  # Desktop GUI (tkinter)
├── setup.sh                # Cross-platform dependency installer
├── Dockerfile              # Web-accessible container (noVNC)
├── docker-compose.yml      # One-command Docker launch
├── docker-entrypoint.sh    # Xvfb + VNC + noVNC startup
├── requirements.txt        # Python deps (pillow)
├── .github/workflows/      # CI: build-windows.yml, build-linux.yml
├── assets/                 # icon.png, logo.png
├── README.md               # this file
└── DEBRIEF.md              # session-by-session dev log
```

---

## 🙏 Acknowledgements

This tool is a thin orchestrator that stands on the shoulders of:

### FFmpeg
[ffmpeg.org](https://ffmpeg.org) — the world's leading multimedia framework.
Salvage uses ffmpeg's `freezedetect`, `select`, `setpts`, and encoder filters.

### Untrunc
[github.com/anthwlock/untrunc](https://github.com/anthwlock/untrunc) —
rebuilds missing/corrupt `moov` index atoms using a healthy reference.

---

*Free to use, modify, and share.*
