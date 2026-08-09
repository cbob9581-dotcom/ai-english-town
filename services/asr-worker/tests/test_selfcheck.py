import os

from asr_worker import selfcheck


def test_selfcheck_loads_asr_model_from_env(monkeypatch):
    monkeypatch.setenv("ASR_MODEL", "distil-large-v3")
    calls: dict = {}

    class _FakeEngine:
        device = "cpu"
        model_name = "distil-large-v3"

        def transcribe(self, audio):
            return {"text": "hello world", "segments": [], "language": "en",
                    "avg_logprob": -0.1, "words": []}

    def _fake_load(device, model=None):
        calls["device"] = device
        calls["model"] = model
        return _FakeEngine()

    monkeypatch.setattr(selfcheck.WhisperEngine, "load", staticmethod(_fake_load))
    result = selfcheck.run(wav_path=None)
    assert calls["model"] == "distil-large-v3"   # 硬编码 "auto" 时此断言失败
    assert result["transcribe_ok"] is True
