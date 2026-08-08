"""faster-whisper 封装。device 决策：cuda 优先，失败切 cpu small.en。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from faster_whisper import WhisperModel


@dataclass
class WhisperEngine:
    model: Any
    device: str
    model_name: str = ""

    @classmethod
    def load(cls, device: str = "auto", model: str | None = None) -> "WhisperEngine":
        if device == "auto":
            try:
                name = model or "distil-large-v3"
                return cls(WhisperModel(name, device="cuda", compute_type="float16"), "cuda", name)
            except Exception:
                name = model or "small.en"
                return cls(WhisperModel(name, device="cpu", compute_type="int8"), "cpu", name)
        name = model or "distil-large-v3"
        return cls(WhisperModel(name, device=device, compute_type="float16"), device, name)

    def transcribe(self, audio: Any, final: bool = False, *, word_timestamps: bool = False) -> dict:
        segments, info = self.model.transcribe(
            audio, language="en", beam_size=3 if final else 1, vad_filter=False,
            word_timestamps=word_timestamps,
        )
        segs = []
        words = []
        for s in segments:
            segs.append({"start": s.start, "end": s.end, "text": s.text.strip()})
            if word_timestamps:
                for w in (s.words or []):
                    words.append({"word": w.word, "start": w.start, "end": w.end,
                                  "probability": w.probability})
        out = {"text": " ".join(s["text"] for s in segs).strip(),
               "segments": segs, "language": info.language,
               "avg_logprob": float(info.avg_logprob)}
        if word_timestamps:
            out["words"] = words
        return out


def word_timestamps_active(engine, env_enable: bool, min_model: str) -> bool:
    """门槛：env 开 + 模型名 >= min_model（startswith 前缀匹配）。否则自动禁用（回退 utterance 级）。"""
    return env_enable and (engine.model_name or "").startswith(min_model)
