"""faster-whisper 封装。device 决策：cuda 优先，失败切 cpu small.en。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from faster_whisper import WhisperModel


@dataclass
class WhisperEngine:
    model: Any
    device: str

    @classmethod
    def load(cls, device: str = "auto") -> "WhisperEngine":
        if device == "auto":
            try:
                return cls(WhisperModel("distil-large-v3", device="cuda", compute_type="float16"), "cuda")
            except Exception:
                return cls(WhisperModel("small.en", device="cpu", compute_type="int8"), "cpu")
        return cls(WhisperModel("distil-large-v3", device=device, compute_type="float16"), device)

    def transcribe(self, audio: Any, final: bool = False) -> dict:
        segments, info = self.model.transcribe(
            audio, language="en", beam_size=3 if final else 1, vad_filter=False,
        )
        segs = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
        return {
            "text": " ".join(s["text"] for s in segs).strip(),
            "segments": segs,
            "language": info.language,
            "avg_logprob": float(info.avg_logprob),
        }
