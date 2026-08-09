"""GOP 先算后写客户端：run_round 成功后、record_round 前（锁外）调 asr-worker /pronounce。
词窗从授权落盘 WAV 按 whisper word_timestamps 切出（双侧 pad + clamp 到边界）；
缺词窗/未命中/降级/超时 → 该词跳过（无 evidence，回退词级代理）。"""
from __future__ import annotations

import base64
import wave
from pathlib import Path

import httpx


def _slice_wav_pcm(wav_path, start_s: float, end_s: float) -> bytes | None:
    """整段 16k mono PCM16 WAV → [start_s, end_s) 的原始 PCM 切片（clamp 到边界）。
    格式不符/越界空窗 → None。"""
    try:
        with wave.open(str(wav_path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            sr = w.getframerate()
            n = w.getnframes()
            i0 = max(0, int(start_s * sr))
            i1 = min(n, int(end_s * sr))
            if i1 <= i0:
                return None
            w.setpos(i0)
            return w.readframes(i1 - i0)
    except Exception:
        return None


def _word_window(word: dict, *, pad_s: float, min_s: float) -> tuple[float, float] | None:
    """whisper 词级时间戳（cross-attention，误差几十~百 ms）双侧 pad + clamp。
    原始词窗 < min_s → None（词窗过短不评）；pad 只是补偿时间戳误差，不豁免过短词。"""
    if word.get("start") is None or word.get("end") is None:
        return None
    raw_start, raw_end = float(word["start"]), float(word["end"])
    if raw_end - raw_start < min_s:
        return None
    start = max(0.0, raw_start - pad_s)
    end = raw_end + pad_s
    return start, end


async def score_words_gop(*, wav_path, target_word_ids, scene_words, words,
                          ipa_by_id, settings, pronounce_url, http_post) -> dict[str, float]:
    """对每个目标词：切词窗 → POST /pronounce → {word_id: gop}。
    http_post(url, body) -> dict（生产 = post_json，测试 = mock）。任何失败/降级 → 跳过。"""
    word_by_text: dict[str, dict] = {}
    for w in words or []:
        t = (w.get("word") or "").strip().lower()
        if t and t not in word_by_text:
            word_by_text[t] = w
    out: dict[str, float] = {}
    for wid in target_word_ids:
        lemma = scene_words.get(wid)
        ipa = ipa_by_id.get(wid)
        if not lemma or not ipa:
            continue
        word = word_by_text.get(lemma.lower())
        if word is None:
            continue
        win = _word_window(word, pad_s=settings.pronunciation_gop_word_pad_ms / 1000.0,
                           min_s=settings.pronunciation_gop_min_word_ms / 1000.0)
        if win is None:
            continue
        pcm = _slice_wav_pcm(wav_path, *win)
        if pcm is None:
            continue
        try:
            resp = await http_post(pronounce_url,
                                   {"wav_b64": base64.b64encode(pcm).decode(), "ipa": ipa})
        except Exception:
            continue
        if not isinstance(resp, dict) or resp.get("degraded") or resp.get("gop") is None:
            continue
        out[wid] = float(resp["gop"])
    return out


async def maybe_score_round_gop(*, store, session_id, utterance_id, settings,
                                scene_words, words, target_word_ids, http_post) -> dict[str, float] | None:
    """先算后写入口：settings 关 / 授权 WAV 缺失 → None（不调 /pronounce）。
    **必须在 events.write_lock 外调用**（锁内调 HTTP+GPU 推理会阻塞全应用证据写入）。"""
    if not getattr(settings, "pronunciation_gop_enabled", False):
        return None
    root = Path(settings.tutor_cache_dir).parent / "pronunciation-audio"
    wav_path = root / session_id / f"{utterance_id}.wav"
    if not wav_path.exists():
        return None
    ipa_by_id: dict[str, str] = {}
    for wid in target_word_ids:
        row = store.get_item("local", wid)
        if row and row["ipa"]:
            ipa_by_id[wid] = row["ipa"]
    # /pronounce 是 HTTP 端点：先 ws:// → http:// 再替换路径（直接 replace 路径会得到
    # "ws://.../pronounce"，httpx 不支持 ws:// scheme 会抛 UnsupportedProtocol 被静默吞掉，
    # 导致全词跳过、GOP 功能永不生效）。
    pronounce_url = settings.asr_ws_url.replace("ws://", "http://").replace("/ws/asr", "/pronounce")
    return await score_words_gop(wav_path=wav_path, target_word_ids=target_word_ids,
                                 scene_words=scene_words, words=words, ipa_by_id=ipa_by_id,
                                 settings=settings, pronounce_url=pronounce_url, http_post=http_post)


async def post_json(url: str, body: dict) -> dict:
    """生产 http_post：httpx 单次调用（与 workers.py 模式一致）。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()
