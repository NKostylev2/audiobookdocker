"""End-to-end tests of scripts/generate_chapter_vid_voice.py on fake torch/cosyvoice/whisper (tests/stubs).

The generator runs as a real subprocess. AUDIOBOOK_ROOT_DIR points it at a temp dir, so nothing of the real
project (voices, output, Telegram) is touched, and --no-telegram skips Telegram configuration entirely.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fanfic_pipeline.exit_codes import (
    EXIT_NO_AUDIO, EXIT_SEGMENTS_FAILED, EXIT_TEXT_MISSING, EXIT_VOICE_MISSING,
)

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "scripts" / "generate_chapter_vid_voice.py"
STUBS = Path(__file__).resolve().parent / "stubs"

TEXT = ("Гарри долго смотрел на закрытую дверь, и не понимал, почему никто не отвечает на стук.\n\n"
        "Рон пожал плечами и сказал, что им стоит подождать до утра, потому что ночью в замке опасно.\n")


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "root"
    (root / "CosyVoice").mkdir(parents=True)
    voices = root / "voices"
    voices.mkdir()
    (voices / "fake.wav").write_bytes(b"RIFF")
    (voices / "fake.reference.txt").write_text("Тестовый референсный текст голоса.", encoding="utf-8")
    text = tmp_path / "chapter.txt"
    text.write_text(TEXT, encoding="utf-8")
    out = tmp_path / "out"
    return {"root": root, "voice": voices / "fake.wav", "text": text, "out": out}


def run_generator(sb, mode="always_raise", text=None, voice=None):
    env = dict(os.environ)
    env.update({
        "AUDIOBOOK_ROOT_DIR": str(sb["root"]),
        "PYTHONPATH": os.pathsep.join([str(STUBS), str(REPO)]),
        "FAKE_TTS_MODE": mode,
    })
    cmd = [sys.executable, str(GENERATOR),
           "--text-path", str(text or sb["text"]),
           "--output-dir", str(sb["out"]),
           "--voice-file", str(voice or sb["voice"]),
           "--no-telegram"]
    return subprocess.run(cmd, env=env, cwd=sb["root"], capture_output=True, text=True, timeout=120)


def test_missing_text_exits_nonzero(sandbox):
    """Was: `return` -> exit code 0 -> supervisor believed generation succeeded."""
    p = run_generator(sandbox, text=sandbox["root"] / "nope.txt")
    assert p.returncode == EXIT_TEXT_MISSING, p.stdout + p.stderr


def test_missing_voice_exits_nonzero(sandbox):
    p = run_generator(sandbox, voice=sandbox["root"] / "voices" / "nope.wav")
    assert p.returncode == EXIT_VOICE_MISSING, p.stdout + p.stderr


def test_empty_chapter_exits_nonzero(sandbox):
    sandbox["text"].write_text("", encoding="utf-8")
    p = run_generator(sandbox)
    assert p.returncode == EXIT_NO_AUDIO, p.stdout + p.stderr


def test_unsynthesizable_segments_fail_loudly_and_no_master_is_built(sandbox):
    """Was: warning + `continue` -> the book silently lost those paragraphs, master built with holes."""
    p = run_generator(sandbox, mode="always_raise")
    assert p.returncode == EXIT_SEGMENTS_FAILED, p.stdout + p.stderr
    assert not list(sandbox["out"].glob("*.wav")) or all("seg_" in f.name for f in sandbox["out"].glob("*.wav"))
    assert not list(sandbox["out"].glob("*.mp3"))
    status = json.loads((sandbox["out"] / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed" and status["error"] == "segments_failed"
    assert len(status["failed_segments"]) >= 1


def test_dead_llm_thread_is_a_failure_not_truncated_audio(sandbox):
    """Real CosyVoice(stream=False) swallows exceptions of its LLM thread and returns partial audio.
    Without the guard this run would continue with that 'result' (and crash later in the stub pipeline)."""
    p = run_generator(sandbox, mode="thread_error")
    assert p.returncode == EXIT_SEGMENTS_FAILED, p.stdout + p.stderr
    assert "llm_job" in (p.stdout + p.stderr)      # the thread's traceback / failure is visible, not hidden


def test_stale_verification_report_is_invalidated_by_a_new_run(sandbox):
    sandbox["out"].mkdir(parents=True)
    report = sandbox["out"] / "gpu_verification_report.json"
    report.write_text(json.dumps({"total_segments": 2, "issues_count": 0, "issues": []}), encoding="utf-8")
    run_generator(sandbox, mode="always_raise")
    assert not report.exists()
