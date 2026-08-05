"""Cross-language source of truth: the JSON Schemas under schemas/."""
import json
from pathlib import Path

import jsonschema
from jsonschema import ValidationError, validate
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def _load(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


def _registry() -> Registry:
    """Register every local schema under its $id so cross-file $refs resolve."""
    resources = {}
    for path in _SCHEMAS.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources[schema["$id"]] = Resource.from_contents(
            schema, default_specification=DRAFT202012
        )
    return Registry().with_resources(resources.items())


_REGISTRY = _registry()


def _validate(doc: dict, schema_name: str) -> None:
    validator = jsonschema.Draft202012Validator(
        _load(schema_name), registry=_REGISTRY
    )
    validator.validate(doc)


def validate_scene_plan(doc: dict) -> None:
    """Raise jsonschema.ValidationError if doc is not a valid ScenePlan."""
    _validate(doc, "scene-plan.schema.json")


def validate_archetype(doc: dict) -> None:
    """Raise jsonschema.ValidationError if doc is not a valid Archetype."""
    _validate(doc, "archetype.schema.json")
