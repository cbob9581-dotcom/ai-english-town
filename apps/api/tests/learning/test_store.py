import json
from pathlib import Path

import pytest

from app.event_store import EventStore
from app.learning.store import LearningStore


@pytest.fixture()
def store(tmp_path) -> LearningStore:
    events = EventStore(tmp_path / "e.db")
    return LearningStore(events.connection)


def test_import_and_resolve(store) -> None:
    imported, known, missing, total = store.import_words(
        "local", "l1", "bakery list",
        [{"lemma": "loaf", "pos": "n", "sense": "1", "ipa": "/loʊf/", "cefr": "A2",
          "scene_tags": json.dumps(["bakery"]), "carrier": "object",
          "slot_categories": json.dumps(["food"]), "source": "quest", "created_at": "2026-08-08T00:00:00+00:00"}])
    assert (imported, known, missing, total) == (1, 0, 0, 1)
    item = store.get_item("local", "word_loaf_n_1")
    assert item["lemma"] == "loaf"
    assert json.loads(item["scene_tags"]) == ["bakery"]
    assert store.resolve_word_id_from_store("loaf", "n") == "word_loaf_n_1"


def test_import_known_dedup(store) -> None:
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                             "scene_tags": "[]", "carrier": "phrase",
                                             "slot_categories": "[]", "source": "quest",
                                             "created_at": "2026-08-08T00:00:00+00:00"}])
    imported, known, missing, total = store.import_words(
        "local", "l2", "y", [{"lemma": "loaf", "pos": "n", "sense": "1",
                              "scene_tags": "[]", "carrier": "phrase", "slot_categories": "[]",
                              "source": "quest", "created_at": "2026-08-08T00:00:00+00:00"}])
    assert (imported, known) == (0, 1)


def test_mastery_crud(store) -> None:
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                             "scene_tags": "[]", "carrier": "phrase",
                                             "slot_categories": "[]", "source": "quest",
                                             "created_at": "2026-08-08T00:00:00+00:00"}])
    assert store.get_mastery("local", "word_loaf_n_1") is None
    store.upsert_mastery("local", "word_loaf_n_1", due="2026-08-09T00:00:00+00:00", state="review")
    m = store.get_mastery("local", "word_loaf_n_1")
    assert m["state"] == "review"
    store.upsert_mastery_counts("local", "word_loaf_n_1", attempts=2, success_count=1,
                                scaffolded_success_count=1)
    m = store.get_mastery("local", "word_loaf_n_1")
    assert m["attempts"] == 2 and m["success_count"] == 1


def test_evidence_dedup_and_list(store) -> None:
    # FK ON：证据必须落在已导入词上
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                             "scene_tags": "[]", "carrier": "phrase",
                                             "slot_categories": "[]", "source": "quest",
                                             "created_at": "2026-08-08T00:00:00+00:00"}])
    ev = {"evidence_id": "ev_1", "event_seq": 1, "session_id": "s1", "attempt_id": "a1",
          "turn_id": "t1", "objective_id": "obj_plaza_w1", "word_id": "word_loaf_n_1",
          "source": "prompted_production", "prompt_level": 1, "axis": "productive",
          "result": "success", "confidence": 0.86,
          "evidence_policy_version": "v1", "fsrs_algorithm_version": "fsrs-5",
          "created_at": "2026-08-08T00:00:00+00:00"}
    store.add_evidence("local", ev)
    store.add_evidence("local", ev)          # INSERT OR IGNORE 去重
    rows = store.evidence_for_word("local", "word_loaf_n_1")
    assert len(rows) == 1


def test_outbox_push_drain(store) -> None:
    store.outbox_push("local", json.dumps({"evidenceId": "ev_1"}))
    store.outbox_push("local", json.dumps({"evidenceId": "ev_2"}))
    drained = store.outbox_drain("local")
    assert [json.loads(p)["evidenceId"] for p in drained] == ["ev_1", "ev_2"]
    assert store.outbox_drain("local") == []   # 二次排空为空


def test_list_items_by_scene_json_each(store) -> None:
    store.import_words(
        "local", "l1", "bakery list",
        [{"lemma": "loaf", "pos": "n", "sense": "1", "ipa": "/loʊf/", "cefr": "A2",
          "scene_tags": json.dumps(["bakery"]), "carrier": "object",
          "slot_categories": json.dumps(["food"]), "source": "quest",
          "created_at": "2026-08-08T00:00:00+00:00"}])
    bakery = store.list_items_by_scene("local", "bakery")
    assert len(bakery) == 1
    assert bakery[0]["lemma"] == "loaf"
    assert store.list_items_by_scene("local", "farm") == []
