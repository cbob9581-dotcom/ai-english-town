import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.event_store import EventStore
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
ARCHETYPE_DOC = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


def test_scene_endpoint_returns_compiled_scene(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    r = client.get("/api/scenes/scene_bakery_001")
    assert r.status_code == 200
    body = r.json()
    assert body["archetypeId"] == "bakery"
    assert any(e["component"] == "companion" for e in body["entities"])


def test_archetypes_endpoint(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    r = client.get("/api/archetypes")
    assert r.status_code == 200
    assert "bakery" in r.json()["ids"]


def test_health(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"


def test_dev_archetypes_preview(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    r = client.get("/api/dev/archetypes")
    assert r.status_code == 200
    body = r.json()
    ids = {a["archetypeId"] for a in body}
    assert {"plaza", "bakery"} <= ids
    plaza = next(a for a in body if a["archetypeId"] == "plaza")
    assert plaza["skeleton"]["exits"] and len(plaza["skeleton"]["entities"]) <= 40
    assert "zones" in plaza and "propSlots" in plaza
