#!/usr/bin/env python3
"""salvage.py — check + repair corrupt video (frame extraction).

Two run modes:
  --mode check   (default) read-only assessment: measure damage, report
                 how much real content is left, size, duration, resolution,
                 audio, error signature, estimated repair outcome. No output.
  --mode repair  run the check, then perform the repair (freeze-trim +
                 re-encode into a clean continuous video).

After ANY run, a report is printed to the console; the user is then asked
whether to also save it as a CSV (interactive prompt; or --report <path>
for non-interactive use).

100% deterministic: pure Python orchestrating ffmpeg filters
(freezedetect, select, setpts, encoders). No AI/LLM at runtime.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

# ---- PyInstaller bootstrap ------------------------------------------------
# When frozen into an exe, bundled tools (ffmpeg/ffprobe/untrunc) live in
# sys._MEIPASS; put it on PATH so shutil.which/subprocess can find them.
if getattr(sys, "frozen", False):
    _bundle = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    if _bundle and _bundle not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _bundle + os.pathsep + os.environ.get("PATH", "")

FREEZE_START_RE = re.compile(r"freeze_start:\s*([0-9.]+)")
FREEZE_END_RE = re.compile(r"freeze_end:\s*([0-9.]+)")
TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm",
              ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".m2ts"}

CONTAINERS = {
    "mkv": "matroska", "mp4": "mp4", "mov": "mov", "avi": "avi",
    "ts": "mpegts", "webm": "webm", "m4v": "ipod", "flv": "flv",
}

CODECS = {
    "h264": "libx264", "hevc": "libx265", "vp9": "libvpx-vp9",
    "av1": "libsvtav1",
}

CONTAINER_CODECS = {
    "mkv":   {"h264", "hevc", "vp9", "av1"},
    "mp4":   {"h264", "hevc"},
    "mov":   {"h264", "hevc"},
    "avi":   {"h264"},
    "ts":    {"h264", "hevc"},
    "webm":  {"vp9", "av1"},
    "m4v":   {"h264", "hevc"},
    "flv":   {"h264"},
}

AUDIO_MODES = ("off", "copy", "aac")

# distinctive error signatures -> human-readable class
ERROR_SIGNATURES = [
    (re.compile(r"moov atom not found", re.I), "truncated/phantom moov (index missing)"),
    (re.compile(r"Header missing", re.I), "broken stream header"),
    (re.compile(r"Invalid NAL unit", re.I), "corrupt NAL units (bad encode / bit rot)"),
    (re.compile(r"missing picture in access unit", re.I), "corrupt NAL units (bad encode / bit rot)"),
    (re.compile(r"max resync size reached", re.I), "decoder lost sync (truncated stream)"),
    (re.compile(r"non-monotonically increasing dts", re.I), "timestamp disorder (often benign)"),
    (re.compile(r"Error submitting packet", re.I), "unusable packet (corrupt stream)"),
    (re.compile(r"Application provided invalid", re.I), "invalid frames submitted"),
    (re.compile(r"Invalid data found", re.I), "invalid data in stream"),
    (re.compile(r"Broken pipe", re.I), "broken stream pipe"),
    (re.compile(r"Error while opening encoder", re.I), "encoder/container mismatch"),
    (re.compile(r"pts has no value", re.I), "corrupt timestamps (container damage)"),
    (re.compile(r"dts < pcr", re.I), "PCR/DTS disorder (container damage)"),
    (re.compile(r"Could not find codec parameters", re.I), "missing codec info (container damage)"),
    (re.compile(r"invalid dts/pts combination", re.I), "corrupt timestamp data (container damage)"),
]


def run(cmd, timeout=7200):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return p.stdout + "\n" + p.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if e.stdout else ""
        err = (e.stderr or b"").decode("utf-8", "replace") if e.stderr else ""
        return out + "\n" + err + "\nSCAN_TIMEOUT_MARKER\n"


def run_with_progress(cmd, total_s, label="", gui=False):
    """Run a command with a real-time progress bar (parses ffmpeg time=).

    When gui=True, outputs VF98PCT:<pct> lines for the GUI to parse.
    Returns (ok, stderr_text) — ok is True if exit code 0."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace")
    stderr_lines = []
    last_pct = -1
    for line in p.stderr:
        stderr_lines.append(line)
        m = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", line)
        if m and total_s > 0:
            cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            pct = min(100, int(cur / total_s * 100))
            if pct > last_pct:
                last_pct = pct
                if gui:
                    print(f"VF98PCT:{pct}")
                else:
                    bar = "\u2588" * (pct // 5) + "\u00b7" * (20 - pct // 5)
                    print(f"\r  {bar} {pct:3d}%{label}", end="", flush=True)
    p.wait()
    if last_pct >= 0:
        print()  # newline after progress bar
    return p.returncode == 0, "".join(stderr_lines)


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def disk_size(path):
    return os.path.getsize(path)


def sparse_alloc_pct(path):
    """% of the file that actually occupies disk blocks (vs holes)."""
    try:
        st = os.stat(path)
        blocks = getattr(st, "st_blocks", 0) * 512
        size = st.st_size
        if size <= 0 or blocks <= 0:
            return 100.0
        return min(100.0, blocks / size * 100.0)
    except OSError:
        return 100.0


def duration(path):
    """Best-effort claimed duration: ffprobe format, then stream, then
    last-keyframe-based estimate. Broken headers often fail the first two."""
    for probe in (
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
    ):
        out = run(probe)
        for line in out.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return float(line)
            except ValueError:
                continue
    # fallback: last keyframe pts_time + a few seconds
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "packet=pts_time,flags",
               "-of", "csv=p=0", path])
    last = 0.0
    for line in out.splitlines():
        if "K" in line.split(",")[-1] if "," in line else False:
            try:
                t = float(line.split(",")[0])
                last = max(last, t)
            except ValueError:
                pass
    return last + 5 if last > 0 else 0.0


def probe_streams(path):
    """Returns (video_info, audio_info) from ffprobe JSON.
    Captures stdout ONLY — broken headers spew huge stderr NAL noise that
    would corrupt the JSON parse."""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", path],
            capture_output=True, timeout=60)
        out = p.stdout.decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError):
        out = ""
    v = {"codec": "?", "width": "?", "height": "?", "fps": "?"}
    a = {"codec": None}
    try:
        data = json.loads(out)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and v["codec"] == "?":
                v["codec"] = s.get("codec_name", "?")
                v["width"] = s.get("width", "?")
                v["height"] = s.get("height", "?")
                fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or ""
                try:
                    num, _, den = fr.partition("/")
                    if den and int(den):
                        v["fps"] = round(int(num) / int(den), 2)
                except (ValueError, ZeroDivisionError):
                    v["fps"] = "?"
            elif s.get("codec_type") == "audio" and a["codec"] is None:
                a["codec"] = s.get("codec_name", "unknown")
    except (ValueError, KeyError):
        pass
    return v, a


def error_signature(path):
    """First distinctive error class from a quick decode attempt."""
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", path])
    for rx, label in ERROR_SIGNATURES:
        if rx.search(out):
            return label
    # fall back to ffmpeg decode errors
    out2 = run(["ffmpeg", "-v", "error", "-i", path, "-t", "2", "-f", "null", "-"])
    for rx, label in ERROR_SIGNATURES:
        if rx.search(out2):
            return label
    return "none detected"


def normalize_noise(value):
    """Accept flexible noise input and return ffmpeg's freezedetect form.

    Understands:
      60   -> -60dB   (plain number = sensitivity in dB)
      -60  -> -60dB   (already negative)
      60dB -> -60dB   (with suffix)
      -60dB-> -60dB   (ffmpeg-native, passed through)
    Falls back to the default if the input can't be parsed.
    """
    v = str(value).strip()
    if not v:
        return "-60dB"
    if v.startswith("-"):
        v = v[1:]
    if v.lower().endswith("db"):
        v = v[:-2].strip()
    try:
        num = float(v)
    except ValueError:
        return "-60dB"
    # guard sanity: -1dB to -100dB range
    num = max(1.0, min(100.0, abs(num)))
    return f"-{num:.0f}dB"


def _freezedetect_with_progress(cmd, total_duration):
    """Run ffmpeg freezedetect, emitting VF98PCT progress to stdout."""
    out_lines = []
    dur_s = total_duration or 60  # fallback estimate
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        out_lines.append(line)
        m = TIME_RE.search(line)
        if m:
            try:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                t = h * 3600 + mi * 60 + s
                pct = min(100, int(t / dur_s * 100))
                if dur_s > 0:
                    print(f"VF98PCT:{pct}", flush=True)
            except (ValueError, ZeroDivisionError):
                pass
    proc.wait()
    if proc.returncode == 0:
        print("VF98PCT:100", flush=True)
    return "".join(out_lines)


def _merge_frozen_intervals(flat):
    """Merge (timestamp, is_end) pairs into (start, end) intervals."""
    starts = sorted(t for t, is_end in flat if not is_end)
    ends = sorted(t for t, is_end in flat if is_end)
    intervals = []
    for s, e in zip(starts, ends):
        intervals.append((s, e))
    return intervals


def _sampled_freezedetect(path, min_freeze, base_noise, duration, n_slices,
                          gui=False):
    """Run N freezedetect passes on evenly-spaced slices across the file.
    Returns (merged_frozen_intervals, combined_log)."""
    if duration <= 0:
        duration = 60
    slice_len = min(60.0, duration / max(n_slices, 1))
    all_frozen = []
    all_log = []
    # tighter noise with higher strictness: -60 at N=2, -85 at N=49
    noise_db = base_noise + (-5 * (n_slices // 5))
    for i in range(n_slices):
        start = i * (duration - slice_len) / max(n_slices - 1, 1) if n_slices > 1 else 0
        end = min(start + slice_len, duration)
        if gui:
            pct = int((i + 1) / n_slices * 100)
            print(f"VF98PCT:{pct}", flush=True)
            print(f"  slice {i + 1}/{n_slices} [{start:.0f}s-{end:.0f}s] noise={noise_db}dB")
        # run freezedetect on this slice with -ss/-t
        cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(end - start),
               "-i", path,
               "-vf", f"freezedetect=n={noise_db}dB:d={min_freeze}",
               "-an", "-f", "null", "-"]
        out = run(cmd)
        all_log.append(out)
        for rx in [FREEZE_START_RE, FREEZE_END_RE]:
            for m in rx.finditer(out):
                all_frozen.append((float(m.group(1)), rx == FREEZE_END_RE))
    if gui:
        print("VF98PCT:100", flush=True)
    return _merge_frozen_intervals(all_frozen), "\n".join(all_log)


def find_frozen_pts(path, min_freeze, noise_db, gui=False, total_duration=None):
    """freezedetect pass. Returns (frozen_intervals, raw_log).
    When gui=True, streams VF98PCT progress lines to stdout."""
    cmd = ["ffmpeg", "-v", "info", "-i", path,
           "-vf", f"freezedetect=n={noise_db}:d={min_freeze}",
           "-an", "-f", "null", "-"]
    if not gui:
        out = run(cmd)
    else:
        out = _freezedetect_with_progress(cmd, total_duration)
    if "SCAN_TIMEOUT_MARKER" in out:
        print("ERROR: freeze scan timed out")
        sys.exit(1)
    intervals = []
    cur = None
    for line in out.splitlines():
        m = FREEZE_START_RE.search(line)
        if m:
            cur = float(m.group(1))
        m = FREEZE_END_RE.search(line)
        if m and cur is not None:
            intervals.append((cur, float(m.group(1))))
            cur = None
    return intervals, out


def build_filter(good, fps, resolution=None):
    terms = "+".join(f"between(t,{s:.2f},{e:.2f})" for (s, e) in good)
    chain = f"select='{terms}',setpts=N/({fps}*TB)"
    if resolution:
        chain += f",scale={resolution}"
    return chain




def analyze(path, args):
    """Run the full assessment. Returns dict with all report fields."""
    info = {"file": path, "size_bytes": disk_size(path), "alloc_pct": sparse_alloc_pct(path)}
    v, a = probe_streams(path)
    info["resolution"] = f"{v['width']}x{v['height']}"
    info["video_codec"] = v["codec"]
    info["fps"] = v["fps"]
    info["audio_codec"] = a["codec"] if a["codec"] else "none"
    info["claimed_duration"] = duration(path)
    info["error"] = error_signature(path)

    # Sparse detection: if the file barely occupies disk blocks, the data was
    # never actually written (pre-allocated only) — no frame pass can help.
    if info["alloc_pct"] < 30.0:
        info["error"] = ("No data detected - Probably never downloaded, "
                         "only pre-allocated")
        info["sparse"] = True
    else:
        info["sparse"] = False

    if info["sparse"] and not getattr(args, "force_pass", False):
        # No real data on disk — skip the expensive full frame pass.
        # (There are no frames to examine; that's what sparse detection is for.
        #  Use --force-pass to run the pass anyway.)
        info["frozen_count"] = 0
        info["frozen_seconds"] = 0.0
        info["good_seconds"] = 0.0
        info["decodable_pct"] = 0.0
        info["estimated_duration"] = 0.0
        info["estimated_size_bytes"] = 0
        info["_good"] = []
        info["_fix"] = "none"
        return info

    # Strictness 0: pure quick check — error signature only, skip freezedetect
    strictness = getattr(args, "strictness", 1)
    if strictness == 0 and not info["sparse"]:
        info["frozen_count"] = 0
        info["frozen_seconds"] = 0.0
        info["good_seconds"] = info["claimed_duration"]
        info["decodable_pct"] = 100.0
        info["estimated_duration"] = info["claimed_duration"]
        info["estimated_size_bytes"] = info["size_bytes"]
        info["_good"] = [(0.0, info["claimed_duration"])]
        info["_fix"] = "none"
        info["verdict"] = "QUICK (unverified)"
        print("  strictness 0: quick scan only — may miss corruption")
        return info

    # Strictness 1: standard — full freezedetect once
    # Strictness 2-49: N evenly-spaced sampled freezedetect slices
    # Strictness 50: three full passes at different noise thresholds
    if strictness == 1 or strictness >= 50:
        print(f"  decoding for damage assessment (freezedetect, strictness={strictness})...")
        noise_db = normalize_noise(args.noise)
        frozen, log = find_frozen_pts(path, args.min_freeze, noise_db,
                                       gui=getattr(args, "gui", False),
                                       total_duration=info.get("claimed_duration"))
    elif 2 <= strictness <= 49:
        noise_db = normalize_noise(args.noise)
        frozen, log = _sampled_freezedetect(
            path, args.min_freeze, noise_db,
            info.get("claimed_duration", 60),
            strictness,
            gui=getattr(args, "gui", False))
    else:
        frozen, log = [], ""
    frozen_s = sum(e - s for (s, e) in frozen)
    info["frozen_count"] = len(frozen)
    info["frozen_seconds"] = round(frozen_s, 1)

    dur = info["claimed_duration"]
    if dur <= 0:
        dur = frozen[-1][1] + 60 if frozen else 0
    good = []
    pos = 0.0
    for (s, e) in frozen:
        s = max(0.0, s - args.margin)
        e = min(dur, e + args.margin)
        if s > pos:
            good.append((pos, s))
        pos = max(pos, e)
    if pos < dur - 0.5:
        good.append((pos, dur))
    info["good_seconds"] = round(sum(e - s for (s, e) in good), 1)
    info["decodable_pct"] = round(100 * info["good_seconds"] / dur, 1) if dur > 0 else 0.0
    info["estimated_duration"] = info["good_seconds"]
    if dur > 0:
        info["estimated_size_bytes"] = int(info["size_bytes"] * info["good_seconds"] / dur)
    else:
        info["estimated_size_bytes"] = 0
    info["_good"] = good
    # decide recommended fix (used by --fix auto and shown in report)
    err = info["error"].lower()
    if "moov" in err or "index" in err or "header missing" in err:
        info["_fix"] = "untrunc"
    elif "no data detected" in err:
        info["_fix"] = "none"
    elif info["good_seconds"] <= 0:
        info["_fix"] = "none"
    elif info["frozen_seconds"] > 0 or "nal" in err or "corrupt" in err:
        info["_fix"] = "salvage"
    elif "container" in err or "timestamp" in err or "dts" in err:
        info["_fix"] = "remux"
    else:
        info["_fix"] = "remux"
    info["verdict"] = info.get("error", "CHECKED")
    return info


def report_line(info):
    b = info["size_bytes"]
    e = info.get("estimated_size_bytes", 0)
    return (
        f"  size: {human_bytes(b)} ({b:,} B) | alloc: {info['alloc_pct']:.0f}% "
        f"| claimed dur: {info['claimed_duration']:.1f}s | real content: "
        f"{info['good_seconds']:.1f}s ({info['decodable_pct']:.0f}%) "
        f"| est. size: {human_bytes(e)} | res: {info['resolution']} "
        f"| audio: {info['audio_codec']} | err: {info['error']}"
    )


def print_report(info):
    print(f"\n=== CHECK REPORT: {os.path.basename(info['file'])} ===")
    print(f"  file: {info['file']}")
    print(f"  size on disk: {human_bytes(info['size_bytes'])} ({info['size_bytes']:,} B)")
    print(f"  sparse allocation: {info['alloc_pct']:.1f}% (100% = fully written)")
    print(f"  resolution: {info['resolution']} | video: {info['video_codec']} "
          f"| fps: {info['fps']}")
    print(f"  audio: {info['audio_codec']}")
    print(f"  claimed duration (index): {info['claimed_duration']:.1f}s")
    if info.get("_fix") == "untrunc" and info["good_seconds"] <= 0:
        print(f"  real decodable content: not assessable via frame pass "
              f"(moov/index missing) — rebuild via untrunc")
    else:
        print(f"  real decodable content: {info['good_seconds']:.1f}s "
              f"({info['decodable_pct']:.0f}% of claimed)")
    print(f"  frozen stretches: {info['frozen_count']} ({info['frozen_seconds']:.1f}s total)")
    print(f"  estimated repair size: {human_bytes(info.get('estimated_size_bytes', 0))}")
    print(f"  estimated repair duration: {info['estimated_duration']:.1f}s")
    print(f"  error signature: {info['error']}")


def report_csv_header():
    return ("file,size_bytes,alloc_pct,resolution,video_codec,fps,audio_codec,"
            "claimed_duration,good_seconds,decodable_pct,estimated_size_bytes,"
            "error,final_size_bytes,final_duration,final_frozen_seconds,verdict")


def report_csv_row(info):
    return (
        f"\"{info['file']}\",{info['size_bytes']},{info['alloc_pct']:.1f},"
        f"\"{info['resolution']}\",{info['video_codec']},{info['fps']},"
        f"{info['audio_codec']},{info['claimed_duration']:.1f},"
        f"{info['good_seconds']:.1f},{info['decodable_pct']:.1f},"
        f"{info.get('estimated_size_bytes', 0)},{info['error']},"
        f"{info.get('final_size_bytes', '')},{info.get('final_duration', '')},"
        f"{info.get('final_frozen_seconds', '')},\"{info.get('verdict', '')}\""
    )


def remux_one(path, output_path, args, info):
    """Lossless container rebuild: -c copy with metadata+date preserved.
    Best for files whose index is healthy but container structure is broken.
    Falls back to a full salvage re-encode if the remux still has frozen
    stretches (broken-index files)."""
    print("  remuxing (lossless -c copy)...")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", path,
           "-map_metadata", "0", "-map_chapters", "0",
           "-c", "copy", "-f", CONTAINERS[args.container], output_path]
    out = run(cmd)
    if not os.path.exists(output_path):
        print("  ERROR: remux produced no output — falling back to salvage")
        return repair_one(path, output_path, args, info)

    # verify: remux should have ~0 frozen if index was honest
    out_frozen, _ = find_frozen_pts(output_path, args.min_freeze, args.noise)
    of = sum(e - s for (s, e) in out_frozen)
    od = duration(output_path)
    if od > 0 and of < od * 0.02:
        info["final_size_bytes"] = disk_size(output_path)
        info["final_duration"] = round(od, 1)
        info["final_frozen_seconds"] = round(of, 1)
        info["verdict"] = "CLEAN"
        print(f"  output: {output_path}")
        print(f"    size: {human_bytes(info['final_size_bytes'])} | "
              f"duration: {od:.1f}s | frozen: {of:.1f}s | CLEAN")
        return True

    # index lied — the copy inherited frozen/phantom data; use salvage instead
    print(f"  remux output still has {of:.1f}s frozen "
          f"(broken index) — falling back to salvage")
    os.remove(output_path)
    return repair_one(path, output_path, args, info)


def find_reference(path, args):
    """Find a healthy sibling file to use as untrunc reference: same folder,
    same codec+resolution. Returns path or None."""
    d = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(path)
    want_v = None
    try:
        v, _ = probe_streams(path)
        want_v = (v["codec"], v["width"], v["height"])
    except Exception:
        pass
    for fname in sorted(os.listdir(d)):
        if fname == name or os.path.splitext(fname)[1].lower() not in VIDEO_EXTS:
            continue
        cand = os.path.join(d, fname)
        if args.reference and cand != args.reference:
            continue
        try:
            v, _ = probe_streams(cand)
            if v["codec"] == "?":
                continue
            if want_v and want_v != ("?", "?", "?") and \
                    (v["codec"], v["width"], v["height"]) != want_v:
                continue
            # prefer a clearly healthy candidate: no freeze detected
            frozen, _ = find_frozen_pts(cand, args.min_freeze, args.noise)
            if not frozen:
                return cand
        except Exception:
            continue
    return None


def untrunc_one(path, output_path, args, info):
    """Rebuild a missing/corrupt moov index using a healthy reference file
    (same encoder). Uses the native untrunc binary. Needs a reference; if none
    is given or found, falls back to salvage."""
    import shutil
    untrunc = shutil.which("untrunc")
    if not untrunc:
        # self-contained installs keep untrunc next to the tool
        local_dir = os.path.dirname(os.path.abspath(__file__))
        for name in ("untrunc", "untrunc.exe"):
            local = os.path.join(local_dir, name)
            if os.path.isfile(local):
                untrunc = local
                break
    if not untrunc:
        print("  ERROR: 'untrunc' binary not found on PATH "
              "(run setup.sh or install it)")
        info["verdict"] = "FAILED (untrunc missing)"
        return False

    ref = args.reference or find_reference(path, args)
    if not ref:
        print("  ERROR: no reference file found — untrunc needs a healthy "
              "sibling from the same camera/app (same codec+resolution). "
              "Pass --reference <file>. Falling back to salvage.")
        return repair_one(path, output_path, args, info)

    print(f"  untrunc (moov rebuild) using reference: {os.path.basename(ref)}")
    tmp_out = output_path + ".untrunc_tmp.mp4"
    # untrunc writes <input>_fixed.mp4 next to the input; we run it in a temp
    # workdir copy to keep our output path clean
    workdir = os.path.join(args.workdir or tempfile.gettempdir(), "untrunc_work")
    os.makedirs(workdir, exist_ok=True)
    src_copy = os.path.join(workdir, os.path.basename(path))
    ref_copy = os.path.join(workdir, os.path.basename(ref))
    shutil.copy2(path, src_copy)
    shutil.copy2(ref, ref_copy)
    cmd = [untrunc, "-n", ref_copy, src_copy]
    out = run(cmd, timeout=3600)
    fixed = src_copy + "_fixed.mp4"
    if not os.path.exists(fixed):
        # try alternate naming: src_copy_fixed.mp4
        for cand in (fixed, os.path.join(workdir, os.path.splitext(
                os.path.basename(path))[0] + "_fixed.mp4")):
            if os.path.exists(cand):
                fixed = cand
                break
        else:
            print("  ERROR: untrunc produced no output")
            print(out[-600:])
            info["verdict"] = "FAILED (untrunc no output)"
            return False
    shutil.move(fixed, output_path)
    shutil.rmtree(workdir, ignore_errors=True)

    out_frozen, _ = find_frozen_pts(output_path, args.min_freeze, args.noise)
    of = sum(e - s for (s, e) in out_frozen)
    od = duration(output_path)
    ok = of < od * 0.02 if od > 0 else False
    info["final_size_bytes"] = disk_size(output_path)
    info["final_duration"] = round(od, 1)
    info["final_frozen_seconds"] = round(of, 1)
    info["verdict"] = "CLEAN" if ok else "WARNING"
    print(f"  output: {output_path}")
    print(f"    size: {human_bytes(info['final_size_bytes'])} | "
          f"duration: {od:.1f}s | frozen: {of:.1f}s | {info['verdict']}")
    return ok


def repair_one(path, output_path, args, info):
    """Repair: freeze-trim + re-encode. Mutates info with final values."""
    good = info["_good"]
    if not good:
        print("  nothing salvageable — no output produced")
        info["verdict"] = "nothing salvageable"
        return False

    vf = build_filter(good, args.fps, getattr(args, "resolution", None))
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf", vf,
           "-c:v", CODECS[args.codec], "-preset", args.preset,
           "-crf", str(args.crf), "-r", str(args.fps),
           "-an", "-y", output_path]

    print(f"  repairing ({args.codec} / {args.container} / audio {args.audio_mode})...")
    if args.gui:
        print("VF98PHASE:encoding", flush=True)
    ok, stderr_out = run_with_progress(cmd, info["estimated_duration"], gui=args.gui)
    if not os.path.exists(output_path):
        print("  ERROR: no output produced")
        print(stderr_out[-500:])
        info["verdict"] = "FAILED"
        return False

    # If audio was requested, mux it from the source into the output
    if args.audio_mode != "off":
        from shutil import move as _move
        tmp = output_path + ".tmp_video." + args.container
        _move(output_path, tmp)
        # build audio trim filter from the same good intervals used for video
        atrim_parts = [
            f"atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS"
            for (s, e) in good
        ]
        af = ";".join(atrim_parts)
        mux_cmd = ["ffmpeg", "-v", "error", "-i", tmp, "-i", path,
                   "-map", "0:v", "-map", "1:a?"]
        if atrim_parts:
            mux_cmd += ["-af", af]
        if args.audio_mode == "copy":
            mux_cmd += ["-c:a", "copy"]
        else:  # aac
            mux_cmd += ["-c:a", "aac", "-b:a", "128k"]
        mux_cmd += ["-y", output_path]
        print("  muxing audio (partial)...")
        if args.gui:
            print("VF98PHASE:muxing", flush=True)
        ok2, o2 = run_with_progress(mux_cmd, info["estimated_duration"], gui=args.gui)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if not os.path.exists(output_path):
            print("  ERROR: audio mux failed — keeping video-only")
            _move(tmp, output_path)

    out_frozen, _ = find_frozen_pts(output_path, args.min_freeze, args.noise)
    of = sum(e - s for (s, e) in out_frozen)
    od = duration(output_path)
    ok = of < od * 0.02 if od > 0 else False
    info["final_size_bytes"] = disk_size(output_path)
    info["final_duration"] = round(od, 1)
    info["final_frozen_seconds"] = round(of, 1)
    info["verdict"] = "CLEAN" if ok else "WARNING"
    print(f"  output: {output_path}")
    print(f"    size: {human_bytes(info['final_size_bytes'])} | "
          f"duration: {od:.1f}s | frozen: {of:.1f}s | {info['verdict']}")
    return ok


def ask_report_csv(results):
    """Interactive prompt (only when stdin is a TTY). Returns path or None."""
    try:
        if not sys.stdin.isatty():
            return None
        ans = input("\nSave report to CSV? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            return None
        path = input("CSV path: ").strip()
        if not path:
            return None
        return path
    except (EOFError, KeyboardInterrupt):
        return None


def write_csv(path, results):
    if os.path.isdir(path):
        from datetime import datetime
        path = os.path.join(path, f"salvage_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    with open(path, "w", newline="") as f:
        f.write(report_csv_header() + "\n")
        for info in results:
            f.write(report_csv_row(info) + "\n")
    print(f"report saved: {path}")


def resolve_output_path(input_path, proposed_path):
    """Return a path that doesn't already exist and won't overwrite the source.
    Appends (1), (2), etc. to the basename until a free name is found (Windows style)."""
    path = proposed_path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path) or os.path.abspath(path) == os.path.abspath(input_path):
        path = f"{base} ({n}){ext}"
        n += 1
    if path != proposed_path:
        print(f"  NOTE: output path exists — using '{os.path.basename(path)}' instead")
    return path


def safe_output_path(input_path, out_dir, fname, args):
    out = os.path.join(out_dir, fname)
    return resolve_output_path(input_path, out)


def dispatch_fix(path, output_path, args, info):
    """Choose and run the repair method based on --fix (auto = from damage)."""
    fix = args.fix
    if fix == "auto":
        fix = info.get("_fix", "salvage")
    print(f"  fix method: {fix}")
    if fix == "none":
        print("  no fix applies (no data detected / nothing salvageable) — "
              "re-download is the only option")
        info["verdict"] = "NOT FIXABLE"
        return False
    if fix == "remux":
        return remux_one(path, output_path, args, info)
    if fix == "untrunc":
        return untrunc_one(path, output_path, args, info)
    return repair_one(path, output_path, args, info)


def interactive_config(args):
    """Step-by-step walk through every option. For each: 1-2 sentence summary
    of its impact, the current default, and a prompt (blank = keep default)."""
    print("\n=== INTERACTIVE CONFIGURATION ===")
    print("Press Enter to keep the default shown in [brackets].\n")

    def ask(name, explain, default, convert=None, choices=None):
        prompt = f"{explain}\n  [{default}] > "
        while True:
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return default
            if not raw:
                return default
            if choices and raw not in choices:
                print(f"  (choose one of: {', '.join(choices)})")
                continue
            try:
                return convert(raw) if convert else raw
            except ValueError:
                print("  (enter a valid number)")

    # ---- source & output (ask first: everything else depends on these) ----
    src = args.input or ""
    src = ask("source", "Source file or folder. Give a FILE to process one "
              "video, or a FOLDER to batch-process every video in it.",
              src or "(none — required)")
    if not src or src == "(none — required)":
        print("ERROR: a source file or folder is required.")
        sys.exit(1)
    args.input = src

    is_dir = os.path.isdir(src)
    if is_dir:
        args.recursive = ask("recursive", "Scan subdirectories recursively? "
                             "'y' or 'n'.", "n" if not getattr(args, "recursive", False) else "y",
                             convert=lambda v: v.lower() in ("y", "yes"),
                             choices=("y", "n"))
    default_out = args.out_dir or (os.path.join(src, "_salvaged") if is_dir
                                   else os.path.dirname(os.path.abspath(src)))
    out_dir = ask("output folder", "Where the output files go. In batch mode "
                  "each source keeps its name; in single mode this is the "
                  "folder for the fixed file.", default_out)
    args.out_dir = out_dir

    if not is_dir:
        default_name = os.path.splitext(os.path.basename(src))[0] + "_salvaged." + args.container
        fname = ask("output filename", "Name of the fixed output file. "
                    "The extension sets the container (e.g. .mkv, .mp4).",
                    default_name)
        args.output_name = fname
    else:
        args.output_name = None

    args.mode = ask("mode", "Run mode: 'check' only assesses the damage; "
                    "'repair' also fixes it.", args.mode,
                    choices=("check", "repair"))
    args.fix = ask("fix", "Repair method: 'auto' decides from the damage; "
                   "'salvage' trims frozen frames and re-encodes; 'remux' "
                   "rebuilds the container losslessly; 'untrunc' rebuilds a "
                   "missing moov index.", args.fix,
                   choices=("auto", "salvage", "remux", "untrunc", "none"))
    args.container = ask("container", "Output container. mkv is the most "
                         "tolerant; mp4/mov are most compatible with players.",
                         args.container, choices=list(CONTAINERS))
    args.codec = ask("codec", "Video codec. h264 is fastest/most compatible; "
                     "hevc is smaller but slower; vp9/av1 are modern but slow.",
                     args.codec, choices=list(CODECS))
    args.audio_mode = ask("audio-mode", "Audio handling: 'off' drops audio, "
                          "'copy' keeps it if decodable, 'aac' re-encodes it.",
                          args.audio_mode, choices=AUDIO_MODES)
    args.force_pass = ask("force-pass", "Run the full freezedetect pass even "
                          "on sparse files (normally skipped — no data = "
                          "nothing to examine). 'y' or 'n'.",
                          "n" if not args.force_pass else "y",
                          convert=lambda v: v.lower() in ("y", "yes"),
                          choices=("y", "n"))
    args.strictness = ask("strictness", "Scan thoroughness 0-50. "
                          "0=quick (error only, ~5s), "
                          "1=standard (full freezedetect), "
                          "2-49=sample N slices across file, "
                          "50=paranoid (three noise passes).",
                          args.strictness, int)
    args.min_freeze = ask("min-freeze", "How long (seconds) a frozen stretch "
                          "must last before it is trimmed. Lower catches "
                          "short freezes, higher keeps more content.",
                          args.min_freeze, float)
    args.noise = ask("noise", "Pixel-difference threshold for 'same frame'. "
                     "More negative (e.g. -80dB) is stricter — only trims "
                     "perfectly identical frames.", args.noise)
    args.margin = ask("margin", "Safety buffer (seconds) trimmed around each "
                      "frozen zone. 0 = no good frames sacrificed, higher = "
                      "more conservative trimming.", args.margin, float)
    args.crf = ask("crf", "Encode quality: LOWER is BETTER quality but bigger "
                   "file (18 ≈ near-lossless, 23 ≈ standard, 28 ≈ small).",
                   args.crf, int)
    args.preset = ask("preset", "Encode speed/size tradeoff: 'ultrafast' is "
                      "fastest but bigger, 'slow' is slower but smaller.",
                      args.preset)
    args.fps = ask("fps", "Output frame rate. Should match the source's real "
                   "rate or playback speed will be wrong.", args.fps, int)

    print("\nConfiguration complete.\n")
    return args


def list_videos(folder, recursive=False):
    vids = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                    vids.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                vids.append(os.path.join(folder, name))
    return vids


def main():
    ap = argparse.ArgumentParser(
        prog="salvage",
        description=(
            "Check and/or repair corrupt video. Reads the file beginning to "
            "end, measures the damage (frozen/error-concealed stretches), and "
            "in repair mode rebuilds a continuous playable video of the real "
            "motion frames."
        ),
        epilog=(
            "Examples:\n"
            "  python3 salvage.py broken.mp4                    # check only\n"
            "  python3 salvage.py broken.mp4 --mode repair      # check + repair\n"
            "  python3 salvage.py in.mp4 --mode repair --codec hevc --audio-mode aac\n"
            "  python3 salvage.py /path/to/folder --mode repair --out-dir /path/to/fixed\n"
            "  python3 salvage.py in.mp4 --report out.csv       # non-interactive CSV\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", nargs="?", help="input file OR folder (folder = batch). "
                                            "Required unless using --interactive")
    ap.add_argument("--mode", choices=("check", "repair"), default="check",
                    help="check = assess only (default); repair = assess + fix")
    ap.add_argument("--fix", choices=("auto", "salvage", "remux", "untrunc", "none"),
                    default="auto",
                    help="repair method (default auto = decide from damage): "
                         "salvage = freeze-trim + re-encode; "
                         "remux = lossless container rebuild; "
                         "untrunc = moov rebuild via reference file")
    ap.add_argument("--reference", help="healthy reference file for untrunc "
                                        "(optional; auto-searches siblings)")
    ap.add_argument("--workdir", default=tempfile.gettempdir(),
                    help="temp work directory (default: OS temp dir)")
    ap.add_argument("--force-pass", action="store_true",
                    help="run the full frame pass even on sparse files "
                         "(normally skipped: no data = nothing to examine)")
    ap.add_argument("--strictness", type=int, default=1, choices=range(51),
                    help="scan thoroughness 0-50. 0=quick (error only), "
                         "1=full freezedetect, 2-49=N sampled slices, "
                         "50=three noise passes (default 1)")
    ap.add_argument("--recursive", action="store_true",
                    help="when input is a folder, scan subdirectories "
                         "recursively")
    ap.add_argument("--interactive", action="store_true",
                    help="walk through every option step by step, with "
                         "explanations and defaults (blank = keep default)")
    ap.add_argument("--report", help="write CSV report to this path (skip interactive prompt)")
    ap.add_argument("--out-dir", help="output directory (repair; default: beside input)")
    ap.add_argument("--container", choices=CONTAINERS.keys(), default="mkv",
                    help=f"output container: {', '.join(CONTAINERS)} (default mkv)")
    ap.add_argument("--codec", choices=CODECS.keys(), default="h264",
                    help=f"video codec: {', '.join(CODECS)} (default h264)")
    ap.add_argument("--audio-mode", choices=AUDIO_MODES, default="copy",
                    help="audio: off (drop), copy (keep if decodable), aac (re-encode 128k). default off")
    ap.add_argument("--min-freeze", type=float, default=2.0,
                    help="min freeze length to trim, seconds (default 2.0)")
    ap.add_argument("--noise", default="-60dB",
                    help="freezedetect noise threshold; accepts 60, -60, 60dB, "
                         "or -60dB (default -60dB)")
    ap.add_argument("--margin", type=float, default=1.0,
                    help="safety margin around each freeze, seconds (default 1.0)")
    ap.add_argument("--crf", type=int, default=20,
                    help="encoder quality, lower = better (default 20)")
    ap.add_argument("--preset", default="veryfast",
                    help="encoder speed/size preset (default veryfast)")
    ap.add_argument("--fps", type=int, default=50,
                    help="output frame rate (default 50)")
    ap.add_argument("--resolution", default="",
                    help="output resolution, e.g. 1280x720 or -2:720 "
                         "(default: keep source)")
    ap.add_argument("--gui", action="store_true",
                    help="output VF98PCT:<pct> progress lines (for GUI embedding)")
    args = ap.parse_args()
    args.noise = normalize_noise(args.noise)

    if args.interactive:
        args = interactive_config(args)
        args.noise = normalize_noise(args.noise)

    if not args.input:
        print("ERROR: input file or folder is required "
              "(or use --interactive to be guided)")
        sys.exit(1)

    allowed = CONTAINER_CODECS.get(args.container, set())
    if allowed and args.codec not in allowed:
        print(f"WARNING: {args.container} + {args.codec} is unusual "
              f"(typically pairs with: {', '.join(sorted(allowed))})")

    if not os.path.exists(args.input):
        print(f"ERROR: input not found: {args.input}")
        sys.exit(1)

    # ---- collect inputs ----
    if os.path.isdir(args.input):
        videos = list_videos(args.input, recursive=getattr(args, "recursive", False))
        if not videos:
            print(f"no video files found in {args.input}")
            sys.exit(1)
        print(f"batch mode: {len(videos)} video(s) in {args.input} "
              f"[mode={args.mode}]")
        out_dir = args.out_dir or os.path.join(args.input, "_salvaged")
        if args.mode == "repair":
            os.makedirs(out_dir, exist_ok=True)
        results = []
        for v in videos:
            print(f"\n=== {os.path.basename(v)} ===")
            info = analyze(v, args)
            print_report(info)
            ok = True
            if args.mode == "repair":
                base = os.path.splitext(os.path.basename(v))[0]
                out_path = resolve_output_path(v,
                    os.path.join(out_dir, base + "_salvaged." + args.container))
                ok = dispatch_fix(v, out_path, args, info)
            results.append(info)
        print(f"\n=== BATCH SUMMARY ({len(results)} files, mode={args.mode}) ===")
        for info in results:
            verdict = info.get("verdict", "checked")
            print(f"  {os.path.basename(info['file'])}: {verdict}")
    else:
        print(f"single file mode [mode={args.mode}]")
        info = analyze(args.input, args)
        print_report(info)
        results = [info]
        if args.mode == "repair":
            base = os.path.splitext(os.path.basename(args.input))[0]
            out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.input))
            os.makedirs(out_dir, exist_ok=True)
            fname = getattr(args, "output_name", None) or (base + "_salvaged." + args.container)
            out_path = safe_output_path(args.input, out_dir, fname, args)
            dispatch_fix(args.input, out_path, args, info)
            print_report(info)

    # ---- report ----
    print("\n=== FINAL REPORT ===")
    for info in results:
        if info.get("final_size_bytes"):
            print(f"  {os.path.basename(info['file'])}: "
                  f"{human_bytes(info['size_bytes'])} -> "
                  f"{human_bytes(info['final_size_bytes'])} "
                  f"({info['claimed_duration']:.1f}s -> {info['final_duration']:.1f}s) "
                  f"[{info.get('verdict', '?')}]")
        else:
            print(f"  {os.path.basename(info['file'])}: checked "
                  f"({info['good_seconds']:.1f}s real / {info['claimed_duration']:.1f}s claimed)")

    csv_path = args.report
    if not csv_path:
        csv_path = ask_report_csv(results)
    if csv_path:
        write_csv(csv_path, results)

    failed = [i for i in results if i.get("verdict") == "FAILED"]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
