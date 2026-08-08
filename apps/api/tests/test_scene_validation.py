import json
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.llm.proposals import ProposalError, validate_proposal

ROOT = Path(__file__).resolve().parents[3]
CATALOG = Catalog.load(ROOT / "assets")
ARCHETYPE = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


def _ok() -> dict:
    return {"fills": [{"slotId": "counter.main", "conceptId": "concept.food.loaf"}],
            "characters": [{"slotId": "vendor", "npcId": "npc_rosa"}],
            "setting": {"displayName": "Rosewood Bakery", "time": "morning"}}


def test_valid_proposal_roundtrips() -> None:
    cleaned, warns = validate_proposal(_ok(), ARCHETYPE, CATALOG)
    assert cleaned["setting"]["displayName"] == "Rosewood Bakery"
    assert warns == []


def test_concept_not_in_slot_category_dropped_alone() -> None:
    p = _ok()
    p["fills"].append({"slotId": "shelf.top", "conceptId": "concept.food.loaf"})  # shelf 候选无 food
    cleaned, warns = validate_proposal(p, ARCHETYPE, CATALOG)
    assert len(cleaned["fills"]) == 1
    assert any("concept.food.loaf" in w and "shelf.top" in w for w in warns)


def test_unknown_npc_dropped_alone() -> None:
    p = _ok()
    p["characters"].append({"slotId": "vendor", "npcId": "npc_ghost"})
    cleaned, warns = validate_proposal(p, ARCHETYPE, CATALOG)
    assert [c["npcId"] for c in cleaned["characters"]] == ["npc_rosa"]


def test_unknown_slot_raises_whole_plan() -> None:
    p = _ok()
    p["fills"][0]["slotId"] = "no.such.slot"
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_duplicate_slot_raises_whole_plan() -> None:
    p = _ok()
    p["fills"].append({"slotId": "counter.main", "conceptId": "concept.food.apple"})
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_too_many_entities_raises_whole_plan() -> None:
    p = _ok()
    for i in range(45):
        p["fills"].append({"slotId": "counter.main", "conceptId": "concept.food.apple"})
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_non_ascii_display_name_raises() -> None:
    p = _ok()
    p["setting"]["displayName"] = "面包店"
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_invalid_time_raises() -> None:
    p = _ok()
    p["setting"]["time"] = "night"
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)
