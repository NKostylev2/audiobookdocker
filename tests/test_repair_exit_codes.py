"""scripts/auto_repair_chapter.py on fake torch/cosyvoice/whisper (tests/stubs), run as a subprocess."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fanfic_pipeline.exit_codes import EXIT_REPAIR_INCOMPLETE

REPO = Path(__file__).resolve().parents[1]
REPAIR = REPO / "scripts" / "auto_repair_chapter.py"
STUBS = Path(__file__).resolve().parent / "stubs"


@pytest.fixture
def chapter(tmp_path):
    root = tmp_path / "root"
    (root / "CosyVoice").mkdir(parents=True)
    (root / "voices").mkdir()
    chap = tmp_path / "chap"
    (chap / "segments").mkdir(parents=True)
    issues = [{"s_idx": i, "expected_text": "Гарри долго смотрел на закрытую дверь, и не понимал, почему никто не отвечает."}
              for i in (0, 1, 2)]
    (chap / "gpu_verification_report.json").write_text(
        json.dumps({"total_segments": 5, "issues_count": 3, "issues": issues}), encoding="utf-8")
    return root, chap


def run_repair(root, chap, mode):
    env = dict(os.environ, AUDIOBOOK_ROOT_DIR=str(root), FAKE_TTS_MODE=mode,
               PYTHONPATH=os.pathsep.join([str(STUBS), str(REPO)]))
    return subprocess.run([sys.executable, str(REPAIR), "--chapter-dir", str(chap)],
                          env=env, cwd=root, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("mode", ["always_raise", "thread_error"])
def test_failed_repairs_are_reported_and_do_not_abort_the_run(chapter, mode):
    """Was: the first inference exception aborted the whole run (traceback, exit 1, remaining issues never
    attempted); a dead LLM thread produced a truncated 'repair' that was accepted as the best candidate."""
    root, chap = chapter
    p = run_repair(root, chap, mode)
    out = p.stdout + p.stderr
    assert p.returncode == EXIT_REPAIR_INCOMPLETE, out
    for idx in ("0000", "0001", "0002"):          # every flagged segment was still attempted
        assert f"Seg {idx}" in out, out
    assert "Traceback (most recent call last)" not in p.stdout   # handled, not crashed
