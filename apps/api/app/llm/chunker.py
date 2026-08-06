"""流式句子切分：delta 累积 → 完整句子（按 [.!?] 切；20 词强制断句、词边界不断词）。
纯函数、无 IO，直接可测。"""
from __future__ import annotations

import re

_TERMINATOR = re.compile(r"[.!?]")
_WORD = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD.findall(text.strip()))


class SentenceChunker:
    def __init__(self, max_words: int = 20) -> None:
        self.max_words = max_words
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        pos = 0
        while True:
            m = _TERMINATOR.search(self._buf, pos)
            if m is None:
                break
            end = m.end()
            # 终止符后跟数字/字母（3.5 / U.S.）不是句子边界 → 跳过该终止符
            if end < len(self._buf) and self._buf[end].isalnum():
                pos = end
                continue
            sentence = self._buf[:end].strip()
            self._buf = self._buf[end:]
            if sentence:
                out.append(sentence)
            pos = 0
        # 强制断句：缓冲超 max_words 词，在词边界切开
        while _word_count(self._buf) > self.max_words:
            words = _WORD.findall(self._buf)
            sentence = " ".join(words[: self.max_words])
            out.append(sentence)
            rest = " ".join(words[self.max_words :])
            self._buf = rest
        return out

    def finalize(self) -> str:
        remainder = self._buf.strip()
        self._buf = ""
        return remainder
