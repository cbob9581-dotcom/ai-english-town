"""LLM 输出校验边界。超长一律判失败降级，绝不截断（截断会让 TTS 读半句话）。"""
from __future__ import annotations

from app.llm.lexmatch import token_contains

_FORBIDDEN = ("```", "http://", "https://", "www.")


class ProposalError(ValueError):
    pass


def _check(text: str, label: str, max_chars: int) -> None:
    if not text or not text.strip():
        raise ProposalError(f"empty {label}")
    if len(text) > max_chars:
        raise ProposalError(f"{label} too long: {len(text)} > {max_chars}")
    if not text.isascii():
        raise ProposalError(f"non-ascii {label}")
    if any(c in text for c in "\r\n"):
        raise ProposalError(f"newline in {label}")
    if any(f in text.lower() for f in _FORBIDDEN):
        raise ProposalError(f"forbidden token in {label}: {next(f for f in _FORBIDDEN if f in text.lower())}")


def validate_speech(text: str, max_chars: int) -> str:
    _check(text, "speech", max_chars)
    return text


def validate_tutor(word: str, scaffold: str, *, expected_word: str, max_scaffold_chars: int) -> None:
    if word.strip().lower() != expected_word.strip().lower():
        raise ProposalError(f"tutor word mismatch: {word!r} != {expected_word!r}")
    _check(scaffold, "scaffold", max_scaffold_chars)
    if not token_contains(scaffold, expected_word):
        raise ProposalError(f"scaffold missing target word: {expected_word!r}")
