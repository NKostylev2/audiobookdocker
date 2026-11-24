class _Model:
    def transcribe(self, *a, **k):
        return {"text": ""}


def load_model(name, device="cpu"):
    return _Model()
