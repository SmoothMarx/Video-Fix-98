"""Fixtures shared across all salvage CLI tests."""
import os
import pytest
import tempfile
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that run the full pipeline on real files")


# add salvage.py directory for direct imports
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

# ---- test files (use the known-good salvaged files in _TEST) ----
TEST_DATA = "/mnt/inventory/_TEST"


@pytest.fixture(scope="session")
def healthy_mp4():
    """A known-healthy salvaged file: 66.7s, 1280x720, h264, aac, no freezes."""
    p = os.path.join(TEST_DATA, "ssp8505_salvaged.mp4")
    if not os.path.exists(p):
        pytest.skip(f"test file missing: {p}")
    return p


@pytest.fixture(scope="session")
def healthy_mp4_2():
    """Another known-healthy salvaged file: 44.6s, 1280x720, h264, aac."""
    p = os.path.join(TEST_DATA, "ssp5703_salvaged.mp4")
    if not os.path.exists(p):
        pytest.skip(f"test file missing: {p}")
    return p


@pytest.fixture
def out_dir():
    """Temporary directory for repair outputs (cleaned up after test)."""
    with tempfile.TemporaryDirectory(prefix="pytest_vf98_") as td:
        yield td


# ---- fixture freezedetect log snippet for parser tests ----
@pytest.fixture
def frozen_log():
    """Simulated freezedetect log lines covering one freeze interval."""
    return (
        "[freezedetect @ 0x...] n:0 pts:-922337203...\n"
        "[freezedetect @ 0x...] freeze_start: 10.50\n"
        "[freezedetect @ 0x...] freeze_end: 15.75\n"
        "[freezedetect @ 0x...] freeze_duration: 5.25\n"
    )
