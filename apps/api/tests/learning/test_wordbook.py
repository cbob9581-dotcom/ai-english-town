import json
from datetime import datetime, timezone

import pytest

from app.event_store import EventStore
from app.learning.dictionary import Dictionary
from app.learning.store import LearningStore
from app.learning.wordbook import ImportValidationError, run_import, validate_words

NOW = datetime(2026, 8, 8, 0, 0, 0, tzinfo=timezone.utc)


def _dictionary() -> Dictionary:
    import tempfile, pathlib
    d = {"version": 1, "words": [
        {"lemma": "loaf", "pos": "n", "senses": ["面包"], "ipa": "/loʊf/", "cefr": "A2",
         "sceneTags": ["bakery"], "carrier": "object", "slotCategories": ["food"]},
        {"lemma": "order", "pos": "v", "senses": ["点餐"], "ipa": "/ɔːrdər/", "cefr": "A2",
         "sceneTags": ["bakery"], "carrier": "phrase", "slotCategories": []},
    ]}
    root = pathlib.Path(tempfile.mkdtemp()) / "assets"
    (root / "wordbook").mkdir(parents=True)
    (root / "wordbook" / "dictionary.json").write_text(json.dumps(d), encoding="utf-8")
    return Dictionary.load(root)


def _store(tmp_path) -> LearningStore:
    import pathlib
    return LearningStore(EventStore(pathlib.Path(tmp_path) / "e.db").connection)


def test_validate_constraints() -> None:
    assert validate_words(["Loaf", "  buy  ", ""]) == ["loaf", "buy"]
    with pytest.raises(ImportValidationError):
        validate_words(["a" * 65])                      # 超长
    with pytest.raises(ImportValidationError):
        validate_words(["hello!", "ok"])                 # 非法字符
    with pytest.raises(ImportValidationError):
        validate_words(["ok"] * 501)                     # 超量


def test_run_import_matches_dictionary(tmp_path) -> None:
    store = _store(tmp_path)
    res = run_import(store, _dictionary(), "local", ["loaf", "gizmo"], name="l1", now=NOW)
    assert res["imported"] == 2          # loaf 命中词典；gizmo 无元数据仍导入（metadata 留空）
    assert res["missingMetadata"] == 1
    item = store.get_item("local", "word_loaf_n_1")
    assert item["carrier"] == "object"
    assert json.loads(item["slot_categories"]) == ["food"]
    assert item["cefr"] == "A2"


def test_run_import_pos_syntax_and_multisense(tmp_path) -> None:
    store = _store(tmp_path)
    res = run_import(store, _dictionary(), "local", ["loaf/n"], name="l1", now=NOW)
    assert res["imported"] == 1
    assert store.get_item("local", "word_loaf_n_1")["lemma"] == "loaf"
