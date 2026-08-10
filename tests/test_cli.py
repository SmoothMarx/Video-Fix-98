"""Proper test suite for salvage.py (CLI) — so "suite green" actually means something.

Quick:     .venv/bin/pytest tests/test_cli.py
Verbose:   .venv/bin/pytest tests/test_cli.py -v
Fast only: .venv/bin/pytest tests/test_cli.py -v -m "not slow"
"""

import csv
import io
import os
import subprocess
import sys

import pytest

import salvage

pytestmark = []


# ============================================================== pure functions

class TestNormalizeNoise:
    def test_plain_number(self):
        assert salvage.normalize_noise("60") == "-60dB"
        assert salvage.normalize_noise("35") == "-35dB"

    def test_negative_number(self):
        assert salvage.normalize_noise("-60") == "-60dB"

    def test_with_db_suffix(self):
        assert salvage.normalize_noise("60dB") == "-60dB"
        assert salvage.normalize_noise("-60dB") == "-60dB"

    def test_empty_and_invalid_fallback(self):
        assert salvage.normalize_noise("") == "-60dB"
        assert salvage.normalize_noise("abc") == "-60dB"

    def test_clamping(self):
        assert salvage.normalize_noise("0") == "-1dB"
        assert salvage.normalize_noise("150") == "-100dB"


class TestBuildFilter:
    def test_without_resolution(self):
        f = salvage.build_filter([(0, 10), (20, 30)], 50)
        assert "select=" in f
        assert "setpts=" in f
        assert "scale=" not in f
        assert "between" in f

    def test_with_resolution(self):
        f = salvage.build_filter([(0, 10)], 50, "1280x720")
        assert "scale=1280x720" in f

    def test_empty_good_ranges(self):
        f = salvage.build_filter([], 50)
        assert "between" not in f


class TestAudioArgs:
    def test_off(self):
        assert salvage.audio_args("off") == ["-an"]

    def test_copy(self):
        # 'copy' mode passes audio through without extra ffmpeg flags
        assert salvage.audio_args("copy") == []

    def test_aac(self):
        a = salvage.audio_args("aac")
        assert "-c:a" in a and "aac" in a and "-b:a" in a

    def test_unknown_falls_back(self):
        assert salvage.audio_args("flac") == ["-an"]


class TestReportCSV:
    def test_header_has_required_columns(self):
        h = salvage.report_csv_header()
        for col in ("file", "verdict", "final_size_bytes", "error",
                     "claimed_duration", "decodable_pct"):
            assert col in h, col

    def test_row_roundtrips(self):
        info = {
            "file": "/test/a.mp4", "size_bytes": 1000, "alloc_pct": 100.0,
            "resolution": "1280x720", "video_codec": "h264", "fps": 50.0,
            "audio_codec": "aac", "claimed_duration": 100.0,
            "good_seconds": 80.0, "decodable_pct": 80.0,
            "estimated_size_bytes": 800, "error": "none",
            "final_size_bytes": 789, "final_duration": 79.5,
            "final_frozen_seconds": 0, "verdict": "CLEAN",
        }
        row = salvage.report_csv_row(info)
        assert "1280x720" in row
        assert "CLEAN" in row
        assert "a.mp4" in row


class TestReportCsvModuleLevel:
    """verify report_csv_row integrates with freezedetect output"""

    def test_write_then_read(self, healthy_mp4, out_dir):
        rp = os.path.join(out_dir, "test_report.csv")
        r = subprocess.run(
            [sys.executable, "salvage.py", healthy_mp4,
             "--mode", "repair", "--out-dir", out_dir, "--fix", "salvage",
             "--container", "mkv", "--report", rp],
            capture_output=True, text=True, timeout=550, cwd=os.path.dirname(__file__) + "/..",
        )
        assert r.returncode == 0, r.stderr[-800:]
        assert os.path.exists(rp)
        with open(rp, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        assert row["verdict"].startswith("CLEAN"), f"verdict was: {row['verdict']}"
        assert row["resolution"] == "1280x720"


# ============================================================== CLI integration

class TestCheckMode:
    def test_healthy_file(self, healthy_mp4, out_dir):
        rp = os.path.join(out_dir, "check.csv")
        r = subprocess.run(
            [sys.executable, "salvage.py", healthy_mp4,
             "--mode", "check", "--report", rp],
            capture_output=True, text=True, timeout=550, cwd=os.path.dirname(__file__) + "/..",
        )
        assert r.returncode == 0, r.stderr[-800:]
        assert "real decodable content" in r.stdout
        assert os.path.exists(rp)

    def test_sparse_file_reports_correct_label(self, out_dir):
        sparse = os.path.join(out_dir, "sparse.mp4")
        # create a 1MB file with no data (alloc_pct = 0)
        with open(sparse, "wb") as f:
            f.truncate(1_000_000)
        r = subprocess.run(
            [sys.executable, "salvage.py", sparse,
             "--mode", "check"],
            capture_output=True, text=True, timeout=300, cwd=os.path.dirname(__file__) + "/..",
        )
        assert "No data detected" in r.stdout
        assert "pre-allocated" in r.stdout
        assert "decoding for damage assessment" not in r.stdout  # skipped


class TestRepairMode:
    def test_healthy_repair_produces_output(self, healthy_mp4, out_dir):
        r = subprocess.run(
            [sys.executable, "salvage.py", healthy_mp4,
             "--mode", "repair", "--out-dir", out_dir, "--fix", "salvage",
             "--container", "mkv", "--codec", "h264", "--audio-mode", "off"],
            capture_output=True, text=True, timeout=550, cwd=os.path.dirname(__file__) + "/..",
        )
        assert r.returncode == 0, r.stderr[-800:]
        base = os.path.splitext(os.path.basename(healthy_mp4))[0]
        out = os.path.join(out_dir, base + ".mkv")
        assert os.path.exists(out), f"output missing: {out}"
        assert os.path.getsize(out) > 1000

    @pytest.mark.slow
    def test_repair_output_has_clean_frames(self, healthy_mp4, out_dir):
        """The repaired output must decode with minimal frozen content."""
        r = subprocess.run(
            [sys.executable, "salvage.py", healthy_mp4,
             "--mode", "repair", "--out-dir", out_dir, "--fix", "salvage",
             "--container", "mkv"],
            capture_output=True, text=True, timeout=550, cwd=os.path.dirname(__file__) + "/..",
        )
        assert r.returncode == 0, r.stderr[-800:]
        base = os.path.splitext(os.path.basename(healthy_mp4))[0]
        out = os.path.join(out_dir, base + ".mkv")
        assert os.path.exists(out)
        # verify it's decodable and has zero freezes
        verify = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", out,
             "-vf", "freezedetect=n=-60dB:d=2", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600,
        )
        assert "freeze_start" not in verify.stderr


class TestBatchMode:
    def test_two_files_batch(self, healthy_mp4, healthy_mp4_2, out_dir):
        r = subprocess.run(
            [sys.executable, "salvage.py", os.path.dirname(healthy_mp4),
             "--mode", "repair", "--out-dir", out_dir, "--fix", "salvage",
             "--container", "mkv"],
            capture_output=True, text=True, timeout=900, cwd=os.path.dirname(__file__) + "/..",
        )
        # batch mode processes ALL videos in _TEST — expect many outputs
        assert os.path.isdir(out_dir)
        mkvs = [f for f in os.listdir(out_dir) if f.endswith(".mkv")]
        assert len(mkvs) >= 2, f"expected >=2 mkv outputs, got {len(mkvs)}"
