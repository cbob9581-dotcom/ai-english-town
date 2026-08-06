from __future__ import annotations

import base64
import io
import time
import wave

from fastapi import FastAPI
from pydantic import BaseModel

from tts_worker.chunker import chunk_sentences
from tts_worker.kokoro_engine import KokoroEngine

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
    audio = b"".join(ENGINE.synthesize(c, voice=req.voice) for c in chunks)  # type: ignore[union-attr]
    ms = int((time.perf_counter() - start) * 1000)
    with wave.open(io.BytesIO(audio), "rb") as w:
        sr = w.getframerate()
    return {"audioBase64": base64.b64encode(audio).decode(), "ms": ms, "sampleRate": sr, "chunks": len(chunks)}


@app.post("/cancel")
def cancel() -> dict:
    _CANCEL["flag"] = True
    return {"ok": True}
