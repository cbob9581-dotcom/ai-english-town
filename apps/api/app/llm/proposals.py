"""LLM 输出校验边界。超长一律判失败降级，绝不截断（截断会让 TTS 读半句话）。"""
from __future__ import annotations

import json

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


def validate_proposal(proposal: dict, archetype: dict, catalog) -> tuple[dict, list[str]]:
    """Scene Director 提案校验。整 plan 结构非法 → ProposalError（degraded）；
    单条问题（conceptId/npcId 不在候选）→ 只拒该条，返回清洗后提案 + warnings。"""
    fills = proposal.get("fills")
    chars = proposal.get("characters")
    setting = proposal.get("setting")
    if not isinstance(fills, list) or not isinstance(chars, list) or not isinstance(setting, dict):
        raise ProposalError("malformed proposal")

    slot_ids = {s["slotId"] for s in archetype["propSlots"]}
    npc_slots = {s["slotId"] for s in archetype.get("npcSlots", [])}
    fill_slots = [f.get("slotId") for f in fills]
    if any(s not in slot_ids for s in fill_slots):
        raise ProposalError(f"unknown slotId in fills: {next(s for s in fill_slots if s not in slot_ids)}")
    if len(set(fill_slots)) != len(fill_slots):
        raise ProposalError("duplicate slotId in fills")
    if len(fills) > 40:
        raise ProposalError("too many fills (>40)")
    if any(c.get("slotId") not in npc_slots for c in chars):
        raise ProposalError("unknown character slot")
    if any(not isinstance(c.get("npcId"), str) for c in chars):
        raise ProposalError("character missing npcId")

    display_name = setting.get("displayName")
    if not isinstance(display_name, str) or not display_name or not display_name.isascii() or len(display_name) > 24:
        raise ProposalError("invalid displayName (must be ≤24 ASCII chars)")
    time_ = setting.get("time")
    if time_ not in ("morning", "afternoon", "evening"):
        raise ProposalError("invalid time")

    warnings: list[str] = []
    ok_fills: list[dict] = []
    seen_concepts: set[str] = set()   # 同一概念全场景只允许一次：防 LLM 多槽放相同道具（重复）
    for f in fills:
        slot = f.get("slotId")
        concept_id = f.get("conceptId")
        slot_spec = next((s for s in archetype["propSlots"] if s["slotId"] == slot), None)
        candidates: set[str] = set()
        for cat in (slot_spec or {}).get("categories", []):
            candidates |= {c.concept_id for c in catalog.concepts_in(cat)}
        if concept_id not in candidates:
            warnings.append(f"concept {concept_id!r} not in slot {slot} candidates")
            continue
        if concept_id in seen_concepts:
            warnings.append(f"duplicate concept {concept_id!r} in slot {slot} dropped")
            continue
        seen_concepts.add(concept_id)
        ok_fills.append({"slotId": slot, "conceptId": concept_id})

    ok_chars: list[dict] = []
    for c in chars:
        npc = catalog.npc(c.get("npcId", ""))
        role = next((s["role"] for s in archetype.get("npcSlots", []) if s["slotId"] == c.get("slotId")), None)
        allowed = {n.npc_id for n in catalog.npcs_in(role)} if role else set()
        if npc is None or (role and npc.npc_id not in allowed):
            warnings.append(f"npc {c.get('npcId')!r} not allowed for slot {c.get('slotId')}")
            continue
        ok_chars.append({"slotId": c["slotId"], "npcId": npc.npc_id})

    return {"fills": ok_fills, "characters": ok_chars,
            "setting": {"displayName": display_name, "time": time_}}, warnings
