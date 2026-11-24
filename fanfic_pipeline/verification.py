"""Supervisor-side wrapper around the verification worker.

Fixes a silent-success bug in run_audiobook.py: the old code read gpu_verification_report.json
after the verifier ran, but never removed the report from the *previous* run. If the verifier
crashed (CUDA OOM, missing model, killed) the stale report -- typically "0 issues" -- was read
and the chapter was declared 100% verified.

run_verification() guarantees that a returned `ok=True` means: the verifier process ran, exited
with a code that is part of its contract, and wrote a fresh, parseable report that covers at
least one segment. Anything else is `ok=False` with a human-readable `reason`.
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fanfic_pipeline.exit_codes import EXIT_OK, EXIT_VERIFY_ISSUES


@dataclass(frozen=True)
class VerificationResult:
    ok: bool                 # the verifier produced a trustworthy fresh report
    issues_count: int        # meaningful only when ok is True
    total_segments: int
    returncode: int
    output: str
    reason: str = ""


def _fail(returncode: int, output: str, reason: str) -> VerificationResult:
    return VerificationResult(False, 0, 0, returncode, output, reason)


def run_verification(cmd, report_file, runner=subprocess.run) -> VerificationResult:
    report_file = Path(report_file)

    # A report from an earlier run says nothing about the current state of the chapter.
    try:
        report_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        return _fail(-1, "", f"cannot remove stale report {report_file}: {e}")

    proc = runner(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  text=True, encoding="utf-8", errors="replace")
    output = proc.stdout or ""
    rc = proc.returncode

    if rc not in (EXIT_OK, EXIT_VERIFY_ISSUES):
        return _fail(rc, output, f"verifier exited with code {rc}")
    if not report_file.exists():
        return _fail(rc, output, "verifier exited normally but wrote no report")

    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return _fail(rc, output, f"report is unreadable: {e}")
    if not isinstance(report, dict):
        return _fail(rc, output, "report has an unexpected structure")

    issues = report.get("issues")
    try:
        issues_count = int(report["issues_count"]) if "issues_count" in report else len(issues or [])
        total = int(report.get("total_segments", 0) or 0)
    except (TypeError, ValueError):
        return _fail(rc, output, "report has non-numeric counters")

    if total <= 0:
        return _fail(rc, output, "report covers 0 segments")
    if rc == EXIT_VERIFY_ISSUES and issues_count == 0:
        return _fail(rc, output, "verifier signalled issues (exit 2) but the report lists none")

    # rc == 0 with issues_count > 0 is inconsistent but conservative: the issues are real work.
    return VerificationResult(True, issues_count, total, rc, output)
