import json
from pathlib import Path

import pytest

from scene_schema.models import ScenePlan
from scene_schema.validate import validate_scene_plan

FIXTURE = Path(__file__).parent / "fixtures" / "bakery-plan.json"


def test_fixture_is_valid_scene_plan() -> None:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validate_scene_plan(doc)  # must not raise


def test_pydantic_parses_fixture() -> None:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = ScenePlan.model_validate(doc)
    assert plan.archetypeId == "bakery"
    assert len(plan.fills) == 3


def test_invalid_component_rejected() -> None:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["fills"][0]["entity"]["component"] = "script"
    with pytest.raises(Exception):
        validate_scene_plan(doc)
