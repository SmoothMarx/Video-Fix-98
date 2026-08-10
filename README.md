# <img src="assets/icon.png" width="28">  Video-Fix-98 — Corrupt Video Checker & Repair Tool

<p align="center">
  <img src="assets/logo.png" width="340" alt="Video-Fix-98 logo">
</p>

**Video-Fix-98** is a small, self-contained tool that rescues video files damaged by
corruption, truncation, or broken index structures. It reads the file the way a
player would — frame by frame, beginning to end — finds the stretches that are
frozen or broken, and rebuilds a clean, continuous, playable video containing
**only the genuine motion frames**.

No AI. No cloud. No Docker. Pure Python calling ffmpeg and (optionally)
untrunc, both installed natively on your machine.

---

## ✨ What it does

| Capability | Description |
|---|---|
| 🔍 **Check** | Assess any video and report the damage: how much real content is left, size, duration, resolution, audio, error type, and what's recoverable |
| 🔧 **Repair** | Fix it — automatically choosing the best method for the damage found |
| 🧠 **Auto mode** | Picks the right fix for you: moov missing → untrunc, frozen frames → salvage, container damage → remux |
| 📋 **Reports** | Console report after every run, plus optional CSV export |
| 🎛️ **Interactive** | Guided walkthrough: explains every option in plain language, asks for your values (blank = keep default) |
| 📦 **Batch** | Point it at a folder and it processes every video in it |
| 🛡️ **Safety** | Never overwrites your original — if a name would clash, it auto-appends `_fixed` |

---

## 🧠 The story: why your corrupt videos "freeze"

When a video file is damaged, the decoder usually doesn't show you garbage —
it **freezes the last good frame** and repeats it (this is called *error
concealment*) until it recovers. On top of that, the file's *index* (the
`moov` atom in MP4s) often **lies** — it claims frames and durations that don't
actually exist on disk.

That's why a naive repair often produces a file that keeps the full length but
is full of frozen stretches: **it trusted the lying index.**

Salvage does the opposite. It ignores the index entirely and trusts only the
**actual decode** — what a player really sees. Every frame is examined
individually, the frozen stretches are found and trimmed, and the survivors are
re-timed onto a clean, continuous timeline. The result is smaller, smooth, and
**every frame in it is real content.**

---

## 🚀 Getting started

### 1. Install (self-contained)

```bash
./setup.sh
```

`setup.sh` **detects** what you already have, and installs **only what's
missing** — no blind re-installs. It checks for:

- `ffmpeg` + `ffprobe` — installs via your system package manager if absent
- `untrunc` — builds from source if absent (only needed for moov-rebuilds)

### 1b. Or run the self-contained container (no host installs at all)

The included `Dockerfile` bundles Python, ffmpeg (all encoders), and a
source-built untrunc into one image. Build once, run anywhere:

```bash
# build the image
docker build -t salvage .

# check a file (mount your media read-only + an output dir)
docker run --rm \
  -v /path/to/media:/data:ro \
  -v /path/to/out:/out \
  salvage /data/broken.mp4

# repair a whole folder
docker run --rm \
  -v /path/to/media:/data:ro \
  -v /path/to/out:/out \
  salvage /data --mode repair --out-dir /out
```

Notes:
- Mount your media folder(s) with `-v`; paths inside the container start at
  the mount point (e.g. `/data/...`).
- For `--fix untrunc`, sibling references in the same mounted folder work
  automatically; for cross-folder references pass `--reference /data/ref.mp4`.
- Everything else works identically to the native install — check, repair,
  auto-fix, interactive (`--interactive` needs a TTY: add `-it`),
  reports, batch.

### 2. Check a file (read-only — safe to try on anything)

```bash
python3 salvage.py broken.mp4
```

### 3. Repair it

```bash
python3 salvage.py broken.mp4 --mode repair
```

### 4. Or let the tool guide you

```bash
python3 salvage.py --interactive
```

It asks for your source, output folder, output filename, then walks through
every option with a plain-language explanation and the default value. Press
**Enter** to accept each default.

---

## 📖 Examples

```bash
# Just check — how bad is it, what's recoverable?
python3 salvage.py broken.mp4

# Full repair, defaults (mkv container, h264, audio dropped)
python3 salvage.py broken.mp4 --mode repair

# Repair with quality + audio: HEVC in MP4 with AAC audio
python3 salvage.py in.mp4 --mode repair --codec hevc --audio-mode aac --crf 18

# Repair a whole folder into another folder
python3 salvage.py /path/to/folder --mode repair --out-dir /path/to/fixed \
    --container webm --codec vp9

# Save a CSV report without being prompted
python3 salvage.py in.mp4 --report out.csv

# Force the full frame pass even on a sparse file
python3 salvage.py in.mp4 --force-pass

# Aggressive trim: catch short freezes, no boundary sacrifice
python3 salvage.py in.mp4 --mode repair --min-freeze 0.5 --margin 0
```

---

## 🔧 Run modes

| Mode | What it does |
|---|---|
| `--mode check` *(default)* | Read-only assessment. Reports size on disk, sparse allocation %, resolution, codecs, claimed vs real duration, frozen stretches, **estimated** repair size/duration, and the error signature. No output file is written. |
| `--mode repair` | Runs the check, then repairs. The final report shows the **actual** output size/duration instead of the estimates. |

---

## 🔧 Repair methods (`--fix`)

| Fix | What it does |
|---|---|
| `--fix auto` *(default)* | **Decides the best method automatically** from the damage found |
| `--fix salvage` | The core method: trim frozen stretches, re-encode the real motion frames into a continuous video |
| `--fix remux` | Lossless container rebuild (`-c copy`, metadata + file date preserved). Best when the index is healthy but the container is broken. Falls back to salvage if the index lies. |
| `--fix untrunc` | Rebuilds a missing/corrupt `moov` index using a healthy reference file (same camera/app/encoder). Auto-searches sibling files, or pass `--reference <file>`. |
| `--fix none` | No fix applies — report only |

**How `--fix auto` decides:**

| Damage found | Auto fix |
|---|---|
| `No data detected` (sparse — never downloaded, only pre-allocated) | `none` — re-download is the only option |
| moov missing / broken index / header missing | `untrunc` (falls back to salvage if no reference exists) |
| Frozen stretches / corrupt NAL units | `salvage` |
| Everything decodes, container-level damage only | `remux` |

---

## 🎛️ Variables you can tune

| Argument | Default | What it controls |
|---|---|---|
| `input` | *(required)* | Source **file** or **folder** (folder = batch) |
| `--mode MODE` | `check` | `check` = assess only; `repair` = assess + fix |
| `--fix FIX` | `auto` | Repair method: `auto`, `salvage`, `remux`, `untrunc`, `none` |
| `--reference FILE` | *(auto)* | Healthy reference file for `--fix untrunc` (auto-searches siblings if omitted) |
| `--out-dir DIR` | beside input / `<input>/_salvaged` | Output location |
| `--container NAME` | `mkv` | Output container: `mkv`, `mp4`, `mov`, `avi`, `ts`, `webm`, `m4v`, `flv` |
| `--codec NAME` | `h264` | Video codec: `h264`, `hevc`, `vp9`, `av1` |
| `--audio-mode MODE` | `off` | Audio: `off` (drop), `copy` (keep if decodable), `aac` (re-encode 128k) |
| `--min-freeze N` | `2.0` | Minimum frozen stretch (seconds) to trim. Lower = catches short freezes; higher = keeps more content |
| `--noise "-60dB"` | `-60dB` | Pixel-difference threshold for "same frame". More negative = stricter (only perfectly identical frames are trimmed) |
| `--margin N` | `1.0` | Safety buffer (seconds) around each frozen zone. `0` = no good frames sacrificed |
| `--crf N` | `20` | Encode quality — **lower is better quality but bigger**. 18 ≈ near-lossless, 23 ≈ standard, 28 ≈ small |
| `--preset NAME` | `veryfast` | Encode speed/size: `ultrafast` → `veryfast` → `medium` → `slow` → `veryslow` |
| `--fps N` | `50` | Output frame rate. Should match the source's real rate |
| `--workdir DIR` | `/tmp` | Temp work directory |
| `--force-pass` | off | Run the full frame pass even on sparse files |
| `--interactive` | off | Walk through every option step by step with explanations |
| `--report PATH` | *(prompt)* | Write CSV report to PATH without prompting |
| `-h, --help` | — | Show full usage |

### Container × codec compatibility (warns, doesn't block)

| Container | Works well with |
|---|---|
| `mkv` | h264, hevc, vp9, av1 |
| `mp4` / `mov` / `m4v` | h264, hevc |
| `avi` / `flv` | h264 |
| `ts` | h264, hevc |
| `webm` | vp9, av1 |

---

## 🛡️ Safety & honest limitations

- **Your original is never modified.** Output always goes to a new file; if a
  name would overwrite the source, `_fixed` is appended automatically.
- **Frame classification is by time-range, not per-frame** within the kept
  zones: every frame in a kept range survives (nothing skipped), but
  corrupt-but-*moving* frames (noise, green blocks) are not individually
  detected and would be kept.
- **Re-encode = one quality generation.** The good frames are re-encoded to
  the chosen codec. Lossless copy is physically impossible on broken-index
  files — that's the point of this tool.
- **Sparse files** (never downloaded, only pre-allocated) are detected cheaply
  and skipped unless you pass `--force-pass` — there are no frames to examine.
- **Duration shown for an input may be the lying phantom value**; the output
  duration reflects the real frame count. Frames are preserved.
- **The margin sacrifices a few boundary frames** — set `--margin 0` to avoid.

---

## 📋 Reports

After **any** run, a report is printed to the console:

- Per file: size → final size, claimed duration → final duration, verdict
- Batch mode: a per-file summary list

Then, if run interactively, the tool asks whether to save the report as CSV
(and where). For automation, pass `--report <path>`.

CSV columns: `file, size_bytes, alloc_pct, resolution, video_codec, fps,
audio_codec, claimed_duration, good_seconds, decodable_pct,
estimated_size_bytes, error, final_size_bytes, final_duration,
final_frozen_seconds, verdict` (final columns filled by repair mode only).

---

## 🙏 Acknowledgements

This tool is a thin orchestrator that stands on the shoulders of two
outstanding open-source projects:

### FFmpeg
[ffmpeg.org](https://ffmpeg.org) — the world's leading multimedia framework.
Salvage uses ffmpeg's `freezedetect`, `select`, `setpts`, and encoder filters
for the entire detection and re-encode pipeline, plus `ffprobe` for stream
inspection. Licensed LGPL 2.1+ (this project's typical builds are GPL 2+ when
built with GPL components such as libx264).

### Untrunc
[github.com/anthwlock/untrunc](https://github.com/anthwlock/untrunc) (fork of
[ponchio/untrunc](https://github.com/ponchio/untrunc)) — a specialized tool
that rebuilds missing or corrupt `moov` index atoms in MP4/MOV files using a
healthy reference file from the same camera or app. Salvage uses it for the
`--fix untrunc` path when the damage is a missing index. Licensed GPL-2.0.

**License note:** both projects are free to install and use, including for
commercial purposes, subject to their respective licenses (GPL-family). This
project does not link against them; it invokes them as separate programs, and
their licenses do not restrict the use of this tool.

---

## 📦 Project layout

```
video-salvage/
├── salvage.py      # the tool (Python stdlib only)
├── setup.sh        # self-contained dependency installer (detect → install)
├── Dockerfile      # self-contained container (bundles ffmpeg + untrunc)
└── README.md       # this file
```

Requires: Python 3.8+, ffmpeg/ffprobe, and untrunc (only for `--fix untrunc`).

---

*Free to use, modify, and share. If you turn this into a proper ffmpeg filter
contribution — send it upstream, the world needs it.*
