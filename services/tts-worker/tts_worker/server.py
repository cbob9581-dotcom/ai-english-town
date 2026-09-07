from __future__ import annotations

import base64
import time

from fastapi import FastAPI
from pydantic import BaseModel

from tts_worker.chunker import chunk_sentences
from tts_worker.kokoro_engine import KokoroEngine, wav_bytes

app = FastAPI(title="tts-worker")
ENGINE: KokoroEngine | None = None
_CANCEL = {"flag": False}


@app.on_event("startup")
def _load() -> None:
    global ENGINE
    ENGINE = KokoroEngine().load()


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_bella"


@app.post("/tts")
def synthesize(req: TTSRequest) -> dict:
    if not req.text.strip():
        return {"audioBase64": "", "ms": 0, "sampleRate": 0, "chunks": 0}
    start = time.perf_counter()
    chunks = chunk_sentences(req.text)
    # FIX: accumulate raw PCM per chunk and wrap into ONE WAV container at the end.
    # The old code called ENGINE.synthesize() per chunk (each a *complete* WAV file,
    # header included) and joined those bytes directly — valid only when there was
    # exactly one chunk. Any reply that chunk_sentences() split into 2+ sentences
    # produced a byte stream with extra RIFF/fmt headers embedded mid-stream, which
    # is not a valid WAV file; the browser's decodeAudioData() would silently fail
    # or truncate to the first chunk only.
    pcm_parts: list[bytes] = []
    sr = 16000
    for c in chunks:
        pcm, sr = ENGINE.synthesize_pcm(c, voice=req.voice)  # type: ignore[union-attr]
        pcm_parts.append(pcm)
    audio = wav_bytes(b"".join(pcm_parts), sr)
    ms = int((time.perf_counter() - start) * 1000)
    return {"audioBase64": base64.b64encode(audio).decode(), "ms": ms, "sampleRate": sr, "chunks": len(chunks)}


@app.post("/cancel")
def cancel() -> dict:
    _CANCEL["flag"] = True
    return {"ok": True}
