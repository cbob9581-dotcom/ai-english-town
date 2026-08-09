import asyncio
import wave

import pytest

from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.gop_client import maybe_score_round_gop, score_words_gop
from app.learning.store import LearningStore
from app.settings import Settings


def _write_wav(path, seconds=1.0, sr=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(sr * seconds))


async def _run(coro):
    return await coro


def test_slice_wav_pcm_clamps_to_boundaries(tmp_path):
    from app.learning.gop_client import _slice_wav_pcm
    wav = tmp_path / "u.wav"
    _write_wav(wav, seconds=1.0)
    assert _slice_wav_pcm(wav, 0.0, 0.5) is not None and len(_slice_wav_pcm(wav, 0.0, 0.5)) == 16000
    assert _slice_wav_pcm(wav, 0.5, 2.0) is not None   # end clamp 到 1.0s
    assert _slice_wav_pcm(wav, 2.0, 3.0) is None       # 越界空窗


def test_score_words_gop_pad_and_min_word_ms(tmp_path):
    wav = tmp_path / "u.wav"
    _write_wav(wav, seconds=1.0)
    settings = Settings(pronunciation_gop_word_pad_ms=100, pronunciation_gop_min_word_ms=120)
    calls = []

    async def http_post(url, body):
        calls.append((url, body["ipa"], len(body["wav_b64"])))
        return {"gop": 0.8, "phoneme_scores": {"l": 0.8}, "degraded": False}

    words = [{"word": "loaf", "start": 0.4, "end": 0.6, "probability": 0.95},
             {"word": "a", "start": 0.1, "end": 0.11, "probability": 0.9}]   # 55ms 词窗 → 过短跳过
    scores = asyncio.run(score_words_gop(
        wav_path=wav, target_word_ids={"word_loaf_n_1", "word_a_n_1"},
        scene_words={"word_loaf_n_1": "loaf", "word_a_n_1": "a"},
        words=words, ipa_by_id={"word_loaf_n_1": "/loʊf/", "word_a_n_1": "/ə/"},
        settings=settings, pronounce_url="http://x/pronounce", http_post=http_post))
    assert scores == {"word_loaf_n_1": 0.8}
    assert len(calls) == 1                       # 过短词窗被拦截，不调 /pronounce


def test_score_words_gop_degraded_or_http_error_skipped(tmp_path):
    wav = tmp_path / "u.wav"
    _write_wav(wav, seconds=1.0)
    settings = Settings()

    async def http_post(url, body):
        return {"gop": None, "phoneme_scores": {}, "degraded": True}   # 降级 → 跳过

    words = [{"word": "loaf", "start": 0.1, "end": 0.5, "probability": 0.9}]
    scores = asyncio.run(score_words_gop(
        wav_path=wav, target_word_ids={"word_loaf_n_1"},
        scene_words={"word_loaf_n_1": "loaf"}, words=words,
        ipa_by_id={"word_loaf_n_1": "/loʊf/"}, settings=settings,
        pronounce_url="http://x/pronounce", http_post=http_post))
    assert scores == {}


def test_maybe_score_round_gop_lock_not_held(tmp_path):
    """先算后写时序断言：/pronounce HTTP 调用发生时 events.write_lock 未被持有（锁外）。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    wav = tmp_path / "pronunciation-audio" / "s1" / "u1.wav"   # {tutor_cache_dir.parent}/pronunciation-audio（与 voice_round 落盘路径一致）
    _write_wav(wav)
    seen = []

    async def http_post(url, body):
        seen.append(events.write_lock.locked())   # 应为 False（锁外）
        return {"gop": 0.8, "phoneme_scores": {}, "degraded": False}

    settings = Settings(pronunciation_gop_enabled=True, tutor_cache_dir=tmp_path / "tc")
    scores = asyncio.run(maybe_score_round_gop(
        store=store, session_id="s1", utterance_id="u1", settings=settings,
        scene_words={"word_loaf_n_1": "loaf"}, words=[{"word": "loaf", "start": 0.1, "end": 0.5, "probability": 0.9}],
        target_word_ids={"word_loaf_n_1"}, http_post=http_post))
    assert seen == [False]                       # /pronounce 发生在锁外
    assert scores == {"word_loaf_n_1": 0.8}


def test_maybe_score_round_gop_disabled_or_no_wav_returns_none(tmp_path):
    events = EventStore(tmp_path / "e.db")
    store = LearningStore(events.connection)

    async def http_post(url, body):
        raise AssertionError("不应调用")

    settings = Settings(pronunciation_gop_enabled=False)
    assert asyncio.run(maybe_score_round_gop(
        store=store, session_id="s1", utterance_id="u1", settings=settings,
        scene_words={}, words=None, target_word_ids=set(), http_post=http_post)) is None
    settings2 = Settings(pronunciation_gop_enabled=True, tutor_cache_dir=tmp_path / "tc")
    assert asyncio.run(maybe_score_round_gop(
        store=store, session_id="s1", utterance_id="u1", settings=settings2,
        scene_words={}, words=None, target_word_ids=set(), http_post=http_post)) is None   # 无 WAV → 静默跳过
