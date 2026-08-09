"""GOP 音素级发音评测：wav2vec2 音素 CTC 模型 + torchaudio forced_align。
公式（评审阻塞 3 修正）：margin(p) = log P(p|X_p) − max_{p'≠p} log P(p'|X_p)
  —— 分母排除自身（否则正确音素 margin=0 全塌缩）、排除 CTC blank 帧、分母限英语音素子集。
score_gop 是纯函数（注入 aligner）；生产 aligner = torchaudio + HF wav2vec2，惰性加载。"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from asr_worker.ipa import ENGLISH_ESPEAK_SYMBOLS, IPA_TO_ESPEAK, tokenize_ipa

log = logging.getLogger(__name__)

MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"
BLANK_ID = 0


@dataclass
class AlignResult:
    posteriors: np.ndarray            # (T, C) 概率，含 blank 列
    segments: list[tuple[str, int, int]]   # (espeak 音素, i0, i1) 目标音素帧区间（不含 blank 帧）
    symbol_by_col: dict[int, str]     # 列 → 模型符号名


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _target_segments(paths: list[int], targets: list[int], *, blank: int,
                     id_to_phone: dict[int, str]) -> list[tuple[str, int, int]]:
    """CTC 路径 → 目标音素帧区间 [(phone, i0, i1)]：跳过 blank，按目标顺序分组连续同音素。
    i1 是 run 之后的第一个下标（半开区间 [i0, i1)），供 posteriors[i0:i1] 直接切片。"""
    segs: list[tuple[str, int, int]] = []
    i, n = 0, len(paths)
    while i < n and len(segs) < len(targets):
        if paths[i] == blank:
            i += 1
            continue
        j = i
        while j < n and paths[j] == paths[i]:
            j += 1
        segs.append((id_to_phone.get(paths[i], f"?{paths[i]}"), i, j))
        i = j
    return segs


def expected_phonemes_from_ipa(ipa: str, *, ipa_to_espeak: dict[str, str] | None = None) -> list[str] | None:
    """词典 IPA → espeak 音素序列。任一符号缺映射 → None（该词回退代理，/pronounce 返回 degraded）。"""
    table = IPA_TO_ESPEAK if ipa_to_espeak is None else ipa_to_espeak
    try:
        toks = tokenize_ipa(ipa)
    except ValueError:
        return None
    phones: list[str] = []
    for t in toks:
        e = table.get(t)
        if e is None:
            return None
        phones.append(e)
    return phones


class PhonemeAligner:
    """惰性加载 wav2vec2 音素 CTC 模型；对齐得到目标音素帧区间 + 帧级后验。默认 CPU。"""

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self._model = None
        self._processor = None

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        self._processor = Wav2Vec2Processor.from_pretrained(MODEL_ID)
        self._model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID).to(self.device).eval()

    @property
    def available(self) -> bool:
        try:
            self.load()
            return True
        except Exception as e:  # noqa: BLE001 —— 模型未下载/加载异常 → 全链路回退代理
            log.warning("phoneme model load failed: %s", e)
            return False

    def _symbol_by_col(self) -> dict[int, str]:
        ids = list(range(self._processor.tokenizer.vocab_size))
        toks = self._processor.tokenizer.convert_ids_to_tokens(ids)
        return {i: (t if t is not None else "<pad>") for i, t in enumerate(toks)}

    def align(self, audio: np.ndarray, expected_phonemes: list[str]) -> AlignResult:
        """audio: 16k float32 词窗段。返回 AlignResult（对齐 API 以实机 dump 为准，见 Task 9 头注）。"""
        import torch
        from torchaudio.functional import forced_align
        self.load()
        ids = [self._processor.tokenizer.convert_tokens_to_ids(p) for p in expected_phonemes]
        with torch.inference_mode():
            feats = self._processor(torch.from_numpy(audio).float(),
                                    sampling_rate=16000, return_tensors="pt").input_values
            logits = self._model(feats.to(self.device)).logits[0]     # (T, C)
            probs = torch.softmax(logits, dim=-1)
            input_lengths = torch.tensor([logits.shape[0]])
            target_lengths = torch.tensor([len(ids)])
            paths, _nll = forced_align(logits.cpu(), torch.tensor([ids]),
                                       input_lengths, target_lengths, blank=BLANK_ID)
        id_to_phone = dict(zip(ids, expected_phonemes))
        segments = _target_segments(paths[0].tolist(), ids, blank=BLANK_ID, id_to_phone=id_to_phone)
        return AlignResult(probs.cpu().numpy(), segments, self._symbol_by_col())


def _margin_scores(posteriors: np.ndarray, segments: list[tuple[str, int, int]],
                   english_symbols: frozenset[str], symbol_by_col: dict[int, str]) -> dict[str, float]:
    """每个目标音素：对对齐帧区间求 P(p) 均值，margin = log P(p) − max_{p'≠p, p'∈英语子集} log P(p')。
    blank 列不在英语子集内 → 分母自动排除。
    symbol_by_col 以列下标为 key、符号为 value → 先建符号→列逆查表 col_of。"""
    out: dict[str, float] = {}
    col_of = {sym: col for col, sym in symbol_by_col.items()}
    for phone, i0, i1 in segments:
        block = posteriors[i0:i1]
        if block.shape[0] == 0:
            continue
        p_self = float(block[:, col_of[phone]].mean())
        comps = [float(block[:, c].mean())
                 for c in range(posteriors.shape[1])
                 if c != col_of[phone]
                 and symbol_by_col[c] in english_symbols]
        p_comp = max(comps) if comps else 0.0
        margin = math.log(p_self) - (math.log(p_comp) if p_comp > 0 else 0.0)
        out[phone] = _sigmoid(margin)
    return out


def score_gop(audio_wav: bytes, expected_phonemes: list[str], aligner, *,
              device: str = "cpu", ipa_to_espeak: dict[str, str] | None = None,
              english_symbols: frozenset[str] | None = None) -> dict | None:
    """audio_wav: 目标词音频段（16k mono PCM16）。expected_phonemes: espeak 音素序列。
    aligner 注入（生产 = PhonemeAligner，测试 = mock 固定后验）。
    → {"gop": 0.0..1.0, "phoneme_scores": {phone: 0.0..1.0}, "degraded": False} | None（降级）"""
    if not expected_phonemes:
        return None
    audio = np.frombuffer(audio_wav, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size < 1600:              # < 100ms 音频，无评分意义
        return None
    try:
        res = aligner.align(audio, expected_phonemes)
    except Exception as e:  # noqa: BLE001 —— 对齐异常 → 降级（记日志，不写证据）
        log.warning("forced align failed: %s", e)
        return None
    english = ENGLISH_ESPEAK_SYMBOLS if english_symbols is None else english_symbols
    phoneme_scores = _margin_scores(res.posteriors, res.segments, english, res.symbol_by_col)
    if not phoneme_scores:
        return None
    return {"gop": float(np.mean(list(phoneme_scores.values()))),
            "phoneme_scores": phoneme_scores, "degraded": False}
