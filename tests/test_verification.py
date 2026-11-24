import json
from types import SimpleNamespace

from fanfic_pipeline.verification import run_verification


def make_runner(rc, report=None, raw=None, stdout="", calls=None):
    """Fake subprocess.run: optionally writes a report file, like the real verifier."""
    def runner(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if report is not None or raw is not None:
            path = cmd[-1]    # report path is passed through `cmd` for the fake only
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw if raw is not None else json.dumps(report))
        return SimpleNamespace(returncode=rc, stdout=stdout)
    return runner


def check(tmp_path, runner, stale=None):
    report = tmp_path / "gpu_verification_report.json"
    if stale is not None:
        report.write_text(json.dumps(stale), encoding="utf-8")
    return run_verification(["fake-verifier", str(report)], report, runner=runner), report


CLEAN = {"total_segments": 10, "issues_count": 0, "issues": []}
DIRTY = {"total_segments": 10, "issues_count": 3, "issues": [{}, {}, {}]}


def test_crashed_verifier_never_inherits_a_stale_clean_report(tmp_path):
    """THE regression: previously a crash + leftover '0 issues' report == chapter 'verified'."""
    res, report = check(tmp_path, make_runner(rc=1), stale=CLEAN)
    assert res.ok is False
    assert "exited with code 1" in res.reason
    assert not report.exists()                      # the stale report was removed before the run


def test_killed_verifier_is_a_failure(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=137), stale=CLEAN)    # e.g. OOM-killer
    assert res.ok is False


def test_exit_zero_without_report_is_a_failure(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=0), stale=CLEAN)
    assert res.ok is False and "no report" in res.reason


def test_clean_run(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=0, report=CLEAN))
    assert res.ok and res.issues_count == 0 and res.total_segments == 10


def test_run_with_issues(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=2, report=DIRTY))
    assert res.ok and res.issues_count == 3


def test_exit_two_with_empty_report_is_inconsistent(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=2, report=CLEAN))
    assert res.ok is False


def test_exit_zero_but_report_lists_issues_is_conservative(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=0, report=DIRTY))
    assert res.ok and res.issues_count == 3


def test_corrupt_report_is_a_failure(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=0, raw="{not json"))
    assert res.ok is False and "unreadable" in res.reason


def test_report_covering_zero_segments_is_not_verification(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=0, report={"total_segments": 0, "issues_count": 0, "issues": []}))
    assert res.ok is False


def test_counter_derived_from_issues_when_issues_count_missing(tmp_path):
    res, _ = check(tmp_path, make_runner(rc=2, report={"total_segments": 5, "issues": [{}, {}]}))
    assert res.ok and res.issues_count == 2
