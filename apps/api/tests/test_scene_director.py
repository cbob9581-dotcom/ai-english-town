import json
from pathlib import Path

from app.catalog import Catalog
from app.llm.mock import MockSceneDirector

ROOT = Path(__file__).resolve().parents[3]
CATALOG = Catalog.load(ROOT / "assets")
ARCHETYPE = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


async def test_mock_ok_proposal_fills_every_slot() -> None:
    d = MockSceneDirector("ok")
    p = await d.propose(archetype_id="bakery", archetype=ARCHETYPE, catalog=CATALOG,
                        recent_scenes=[])
    assert len(p["fills"]) == len(ARCHETYPE["propSlots"])
    assert {c["conceptId"] for c in p["fills"]}
    assert p["setting"]["displayName"].isascii()


async def test_mock_partial_fills_keeps_single_slot() -> None:
    d = MockSceneDirector("partial_fills")
    p = await d.propose(archetype_id="bakery", archetype=ARCHETYPE, catalog=CATALOG, recent_scenes=[])
    assert len(p["fills"]) == 1


async def test_mock_timeout_sleeps_long() -> None:
    d = MockSceneDirector("timeout")
    try:
        import asyncio
        async with asyncio.timeout(0.05):
            await d.propose(archetype_id="bakery", archetype=ARCHETYPE, catalog=CATALOG, recent_scenes=[])
        raise AssertionError("应超时")
    except TimeoutError:
        pass
