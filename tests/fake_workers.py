"""Fake worker scripts for orchestrator tests. They are written to a temp dir and executed as real
subprocesses, so the supervisor's process/exit-code/output plumbing is exercised for real.

Behaviour is driven by JSON "plans" in the chapter directory:
  verify_plan.json : list of {"rc": int, "report": dict|null}   (one entry per verifier call)
  repair_plan.json : list of int exit codes                      (one entry per repair call)
  gen_rc.txt       : exit code of the generator (default 0)
Each worker appends a line to <name>.calls so tests can assert what was (not) executed.
"""

GENERATE = r'''
import sys, pathlib
chap = pathlib.Path(sys.argv[sys.argv.index("--text-path") + 1]).parent / "chap"
(chap / "generate.calls").open("a").write("x\n")
rc = int((chap / "gen_rc.txt").read_text()) if (chap / "gen_rc.txt").exists() else 0
print("2026-11-24 [INFO] [1/3] Synth OK (6.10s in 3.20s): fake segment...")
if rc:
    print("Traceback (most recent call last):")
    print('RuntimeError: simulated generator crash')
sys.exit(rc)
'''

VERIFY = r'''
import sys, json, pathlib
chap = pathlib.Path(sys.argv[sys.argv.index("--chapter-dir") + 1])
calls = chap / "verify.calls"
n = len(calls.read_text().splitlines()) if calls.exists() else 0
calls.open("a").write("x\n")
plan = json.loads((chap / "verify_plan.json").read_text())
step = plan[min(n, len(plan) - 1)]
if step.get("report") is not None:
    (chap / "gpu_verification_report.json").write_text(json.dumps(step["report"]))
print("Verification complete")
sys.exit(step["rc"])
'''

REPAIR = r'''
import sys, json, pathlib
chap = pathlib.Path(sys.argv[sys.argv.index("--chapter-dir") + 1])
calls = chap / "repair.calls"
n = len(calls.read_text().splitlines()) if calls.exists() else 0
calls.open("a").write("x\n")
plan = json.loads((chap / "repair_plan.json").read_text())
rc = plan[min(n, len(plan) - 1)]
print("Attempting self-healing repair")
sys.exit(rc)
'''


def install(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name, body in (("gen.py", GENERATE), ("verify.py", VERIFY), ("repair.py", REPAIR)):
        (scripts / name).write_text(body, encoding="utf-8")
    chap = tmp_path / "chap"
    chap.mkdir()
    text = tmp_path / "Глава_01.txt"
    text.write_text("Тестовый текст главы.", encoding="utf-8")
    return scripts, chap, text


def report(issues, total=10):
    return {"total_segments": total, "issues_count": issues, "issues": [{"s_idx": i} for i in range(issues)]}
