"""Guards for failures that third-party code swallows.

CosyVoice runs its LLM decoding in a worker thread (cosyvoice/cli/model.py: llm_job) and, with
stream=False, simply join()s it. If that thread dies (CUDA OOM mid-decode, driver error), Python's
threading machinery prints the traceback to stderr and the caller carries on with whatever speech
tokens had been produced so far -> a *truncated* utterance and no exception. Downstream that looks
exactly like a "swallowed ending".

ThreadExceptionGuard records such exceptions so the caller can turn them into a normal failure.
"""
import threading


class ThreadFailure(RuntimeError):
    """A background thread died while the main thread was waiting for its result."""


class ThreadExceptionGuard:
    def __init__(self):
        self._errors = []
        self._lock = threading.Lock()
        self._prev_hook = None
        self._installed = False

    # -- hook -------------------------------------------------------------------------
    def _hook(self, args):
        if args.exc_type is not SystemExit:
            name = getattr(args.thread, "name", "?")
            with self._lock:
                self._errors.append((name, args.exc_value))
        # keep the default behaviour (traceback on stderr) so nothing is hidden
        if self._prev_hook is not None:
            self._prev_hook(args)

    def install(self):
        if not self._installed:
            self._prev_hook = threading.excepthook
            threading.excepthook = self._hook
            self._installed = True
        return self

    def uninstall(self):
        if self._installed:
            threading.excepthook = self._prev_hook
            self._installed = False

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False

    # -- usage ------------------------------------------------------------------------
    def clear(self):
        """Forget previously recorded errors (call right before the guarded operation)."""
        with self._lock:
            self._errors.clear()

    def raise_if_failed(self):
        """Raise ThreadFailure if any thread died since the last clear()."""
        with self._lock:
            errors, self._errors = list(self._errors), []
        if errors:
            name, exc = errors[0]
            more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            raise ThreadFailure(f"thread '{name}' died: {type(exc).__name__}: {exc}{more}") from exc


def configure_cuda_optimizations():
    """
    Configures NVIDIA Ampere (RTX 30-series / 40-series) TF32 and cuDNN benchmark
    optimizations for high-throughput speech synthesis and vocoder convolutions.
    """
    try:
        import torch
        if torch.cuda.is_available():
            torch.set_float32_matmul_precision("high")
            if hasattr(torch.backends, "cuda") and hasattr(torch.backends.cuda, "matmul"):
                torch.backends.cuda.matmul.allow_tf32 = True
            if hasattr(torch.backends, "cudnn"):
                torch.backends.cudnn.allow_tf32 = True
                torch.backends.cudnn.benchmark = True
    except Exception:
        pass

