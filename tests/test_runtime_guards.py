import threading

import pytest

from fanfic_pipeline.runtime_guards import ThreadExceptionGuard, ThreadFailure


def _run_failing_thread(exc=RuntimeError("CUDA out of memory"), name="llm_job"):
    def boom():
        raise exc
    t = threading.Thread(target=boom, name=name)
    t.start()
    t.join()


@pytest.fixture
def silent_hook():
    """Replace the default hook so test output is not polluted by expected tracebacks."""
    calls = []
    prev = threading.excepthook
    threading.excepthook = lambda args: calls.append(args)
    yield calls
    threading.excepthook = prev


def test_dead_thread_becomes_an_exception(silent_hook):
    with ThreadExceptionGuard() as guard:
        guard.clear()
        _run_failing_thread()
        with pytest.raises(ThreadFailure) as ei:
            guard.raise_if_failed()
    assert "llm_job" in str(ei.value) and "CUDA out of memory" in str(ei.value)
    assert isinstance(ei.value.__cause__, RuntimeError)


def test_no_failure_no_exception(silent_hook):
    with ThreadExceptionGuard() as guard:
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        guard.raise_if_failed()


def test_clear_forgets_earlier_failures(silent_hook):
    with ThreadExceptionGuard() as guard:
        _run_failing_thread()
        guard.clear()
        guard.raise_if_failed()


def test_error_is_reported_once(silent_hook):
    with ThreadExceptionGuard() as guard:
        _run_failing_thread()
        with pytest.raises(ThreadFailure):
            guard.raise_if_failed()
        guard.raise_if_failed()   # already consumed


def test_previous_hook_still_sees_the_traceback(silent_hook):
    """The guard must not hide tracebacks from stderr/logging."""
    with ThreadExceptionGuard():
        _run_failing_thread()
    assert len(silent_hook) == 1


def test_uninstall_restores_hook(silent_hook):
    before = threading.excepthook
    guard = ThreadExceptionGuard().install()
    assert threading.excepthook != before
    guard.uninstall()
    assert threading.excepthook == before


def test_system_exit_in_thread_is_not_an_error(silent_hook):
    with ThreadExceptionGuard() as guard:
        _run_failing_thread(exc=SystemExit(0))
        guard.raise_if_failed()


def test_configure_cuda_optimizations_runs_safely():
    from fanfic_pipeline.runtime_guards import configure_cuda_optimizations
    # Must not raise whether CUDA is present or not
    configure_cuda_optimizations()

