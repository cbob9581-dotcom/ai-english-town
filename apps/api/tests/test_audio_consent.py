import asyncio, wave
from app.event_store import EventStore
from app.voice_round import run_round
from app.settings import Settings

class _State:
    def __init__(self, settings):
        self.settings = settings
        self.scene = None
        self._seq = 0
        self.active_turn_id = None
        self.played_ms = 0
        self.is_playing = False
    def new_turn_id(self):
        self._seq += 1
        return f"turn_{self._seq}"
    @property
    def generation_id(self): return "gen_x"

class _Actor:
    async def stream_reply(self, **kw):
        yield {"type": "npc.speech.delta", "text": "Here is a"}
        await asyncio.sleep(10)   # 阻塞：让 cancel 落在首次 yield 之后的流中途

async def _run(tmp_path, *, consent: bool, interrupted: bool):
    events = EventStore(tmp_path / "e.db")
    settings = Settings(pronunciation_audio_consent=consent, tutor_cache_dir=tmp_path / "tc")
    state = _State(settings)
    pcm = b"\x00\x00" * 1600
    async def asr(audio): return {"finalText": "loaf", "confidence": -0.2, "segments": [], "language": "en"}
    async def tts(t): return {"audioBase64": "AA==", "sampleRate": 24000, "ms": 100}
    async def send(x): pass
    task = asyncio.create_task(run_round("s1", "u1", pcm, events, asr, tts, send, _Actor(), state))
    if interrupted:
        await asyncio.sleep(0.05)   # 让 run_round 越过 asr await、消费首个 delta、进入 actor 阻塞
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    else:
        await task
    return tmp_path / "pronunciation-audio", events

def test_consent_on_success_writes_wav(tmp_path):
    root, events = asyncio.run(_run(tmp_path, consent=True, interrupted=False))
    wav = root / "s1" / "u1.wav"
    assert wav.exists()
    with wave.open(str(wav), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 16000

def test_interrupt_does_not_write(tmp_path):
    root, events = asyncio.run(_run(tmp_path, consent=True, interrupted=True))
    assert not (root / "s1").exists()
    # 取消发生在流中途（asr 已跑、首个 delta 已消费、未 commit）→ 补写部分轮次事件
    turns = [e for e in events.list_after("s1", 0) if e["event_type"] == "dialogue.turn"]
    assert len(turns) == 1

def test_no_consent_does_not_write(tmp_path):
    root, events = asyncio.run(_run(tmp_path, consent=False, interrupted=False))
    assert not (root / "s1").exists()
