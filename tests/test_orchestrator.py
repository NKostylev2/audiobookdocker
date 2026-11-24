import json
import sys

import pytest

import fake_workers as fw
import run_audiobook as ra


@pytest.fixture
def env(tmp_path, monkeypatch):
    scripts, chap, text = fw.install(tmp_path)
    monkeypatch.setattr(ra, "PYTHON_BIN", sys.executable)
    monkeypatch.setattr(ra, "GENERATE_SCRIPT", scripts / "gen.py")
    monkeypatch.setattr(ra, "VERIFY_SCRIPT", scripts / "verify.py")
    monkeypatch.setattr(ra, "REPAIR_SCRIPT", scripts / "repair.py")
    monkeypatch.setattr(ra, "parse_chapter_info", lambda p: {
        "chap_dir": chap, "chap_name": "Глава_01", "book_title": "Тестовая книга", "file_path": p})

    class E:
        pass
    e = E()
    e.chap, e.text = chap, text
    e.verify = lambda plan: (chap / "verify_plan.json").write_text(json.dumps(plan))
    e.repair = lambda plan: (chap / "repair_plan.json").write_text(json.dumps(plan))
    e.gen_rc = lambda rc: (chap / "gen_rc.txt").write_text(str(rc))
    e.calls = lambda name: len((chap / f"{name}.calls").read_text().splitlines()) if (chap / f"{name}.calls").exists() else 0
    e.run = lambda **kw: ra.run_pipeline_for_chapter(text, **kw)
    return e


# ---------------------------------------------------------------- happy path
def test_clean_chapter_is_ok(env, capsys):
    env.verify([{"rc": 0, "report": fw.report(0)}])
    assert env.run() == ra.RESULT_OK
    out = capsys.readouterr().out
    assert "УСПЕШНО ГОТОВА" in out
    assert "100%" not in out.replace("100%|", "")      # the old unconditional claim is gone
    assert env.calls("repair") == 0


# ---------------------------------------------------------------- the headline regression
def test_crashed_verifier_with_stale_clean_report_is_failure_not_success(env, capsys):
    (env.chap / "gpu_verification_report.json").write_text(json.dumps(fw.report(0)))   # stale "0 issues"
    env.verify([{"rc": 1, "report": None}])                                            # verifier crashes
    assert env.run() == ra.RESULT_FAILED
    out = capsys.readouterr().out
    assert "УСПЕШНО ГОТОВА" not in out
    assert "Верификация не выполнена" in out
    assert env.calls("repair") == 0


def test_verifier_exit0_without_report_is_failure(env):
    env.verify([{"rc": 0, "report": None}])
    assert env.run() == ra.RESULT_FAILED


# ---------------------------------------------------------------- repair loop
def test_issues_repaired_in_one_pass(env):
    env.verify([{"rc": 2, "report": fw.report(2)}, {"rc": 0, "report": fw.report(0)}])
    env.repair([0])
    assert env.run() == ra.RESULT_OK
    assert env.calls("verify") == 2 and env.calls("repair") == 1


def test_persistent_issues_are_reported_honestly(env, capsys):
    env.verify([{"rc": 2, "report": fw.report(1)}])           # same plan entry reused: never gets clean
    env.repair([0])
    assert env.run() == ra.RESULT_ISSUES
    out = capsys.readouterr().out
    assert env.calls("repair") == 3
    assert "ТРЕБУЕТ ВНИМАНИЯ" in out
    assert "УСПЕШНО ГОТОВА" not in out
    assert "100%" not in out.replace("100%|", "")


def test_repair_crash_prevents_ok_even_if_reverify_is_clean(env, capsys):
    """If repair died, the master may not have been rebuilt: segment-level verification can't see that."""
    env.verify([{"rc": 2, "report": fw.report(1)}, {"rc": 0, "report": fw.report(0)}])
    env.repair([1])
    assert env.run() == ra.RESULT_ISSUES
    assert "УСПЕШНО ГОТОВА" not in capsys.readouterr().out


def test_later_successful_repair_pass_clears_earlier_repair_failure(env):
    env.verify([{"rc": 2, "report": fw.report(1)}, {"rc": 2, "report": fw.report(1)}, {"rc": 0, "report": fw.report(0)}])
    env.repair([ra.EXIT_REPAIR_INCOMPLETE, 0])
    assert env.run() == ra.RESULT_OK


def test_reverification_failure_is_a_failure(env):
    env.verify([{"rc": 2, "report": fw.report(1)}, {"rc": 1, "report": None}])
    env.repair([0])
    assert env.run() == ra.RESULT_FAILED


# ---------------------------------------------------------------- generation stage
def test_generation_failure_short_circuits_and_shows_worker_output(env, capsys):
    env.gen_rc(13)
    env.verify([{"rc": 0, "report": fw.report(0)}])
    assert env.run() == ra.RESULT_FAILED
    out = capsys.readouterr().out
    assert "код 13" in out
    assert "simulated generator crash" in out           # tail of the worker output (traceback) is shown
    assert env.calls("verify") == 0                      # verification never ran


def test_skip_generation_verifies_only(env):
    env.verify([{"rc": 0, "report": fw.report(0)}])
    assert env.run(skip_generation=True) == ra.RESULT_OK
    assert env.calls("generate") == 0 and env.calls("verify") == 1


# ---------------------------------------------------------------- worker output classification
@pytest.mark.parametrize("line,kind", [
    ("Traceback (most recent call last):", "warn"),
    ("Exception in thread Thread-3 (llm_job):", "warn"),
    ("RuntimeError: CUDA out of memory", "warn"),
    ("2026-11-24 [WARNING] [5/325] Inference failed", "warn"),
    ("2026-11-24 [INFO] [12/325] Synth OK (6.10s in 3.20s): text...", "info"),
    ("2026-11-24 [INFO] [12/325] Swallowed ending detected, retry attempt 1", "info"),
    ("Full WAV generated: /x.wav", "info"),
    ("some irrelevant debug line", None),
])
def test_classify_worker_line(line, kind):
    assert ra.classify_worker_line(line) == kind


# ---------------------------------------------------------------- batch planning
def ch(code, name="Глава"):
    return {"chap_name": f"{name}_{code}", "status": {"code": code}}


def test_plan_batch_includes_generated_and_skips_verified():
    plan = ra.plan_batch([ch("PENDING"), ch("IN_PROGRESS"), ch("HAS_ISSUES"), ch("GENERATED"), ch("VERIFIED_OK")])
    assert [p["chapter"]["status"]["code"] for p in plan] == ["PENDING", "IN_PROGRESS", "HAS_ISSUES", "GENERATED"]
    assert [p["skip_generation"] for p in plan] == [False, False, False, True]
    assert not any(p["clean_cache"] for p in plan)


def test_plan_batch_clean_cache_never_wipes_unfinished_work():
    plan = {p["chapter"]["status"]["code"]: p
            for p in ra.plan_batch([ch("PENDING"), ch("IN_PROGRESS"), ch("HAS_ISSUES"), ch("GENERATED")], clean_cache=True)}
    assert plan["IN_PROGRESS"]["clean_cache"] is False and plan["IN_PROGRESS"]["warning"]
    assert plan["GENERATED"]["clean_cache"] is False and plan["GENERATED"]["warning"]
    assert plan["HAS_ISSUES"]["clean_cache"] is True      # explicitly requested, chapter was complete
    assert plan["PENDING"]["clean_cache"] is True


# ---------------------------------------------------------------- process exit status
def test_exit_code_for():
    assert ra.exit_code_for([]) == 0
    assert ra.exit_code_for([ra.RESULT_OK, ra.RESULT_OK]) == 0
    assert ra.exit_code_for([ra.RESULT_OK, ra.RESULT_ISSUES]) == 3
    assert ra.exit_code_for([ra.RESULT_ISSUES, ra.RESULT_FAILED]) == 1


@pytest.mark.parametrize("results,code", [
    ([ra.RESULT_OK, ra.RESULT_OK], 0),
    ([ra.RESULT_OK, ra.RESULT_ISSUES], 3),
    ([ra.RESULT_OK, ra.RESULT_FAILED], 1),
])
def test_batch_run_exit_status(monkeypatch, tmp_path, results, code):
    chapters = [{"chap_name": f"Глава_{i}", "file_path": tmp_path / f"{i}.txt",
                 "status": {"code": "PENDING"}} for i in range(len(results))]
    it = iter(results)
    monkeypatch.setattr(ra, "scan_all_chapters", lambda: chapters)
    monkeypatch.setattr(ra, "run_pipeline_for_chapter", lambda *a, **k: next(it))
    monkeypatch.setattr(sys, "argv", ["run_audiobook.py", "--all"])
    with pytest.raises(SystemExit) as ei:
        ra.main()
    assert ei.value.code == code


def test_batch_never_claims_success_when_a_chapter_failed(monkeypatch, tmp_path, capsys):
    chapters = [{"chap_name": "Глава_1", "file_path": tmp_path / "1.txt", "status": {"code": "PENDING"}}]
    monkeypatch.setattr(ra, "scan_all_chapters", lambda: chapters)
    monkeypatch.setattr(ra, "run_pipeline_for_chapter", lambda *a, **k: ra.RESULT_FAILED)
    monkeypatch.setattr(sys, "argv", ["run_audiobook.py", "--all"])
    with pytest.raises(SystemExit):
        ra.main()
    out = capsys.readouterr().out
    assert "успешно обработаны" not in out and "ошибкой 1" in out
