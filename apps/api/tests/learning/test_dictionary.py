import json
from pathlib import Path

from app.learning.dictionary import Dictionary


def test_load_and_get(tmp_path) -> None:
    d = {
        "version": 1,
        "words": [
            {"lemma": "loaf", "pos": "n", "senses": ["一条面包"], "ipa": "/loʊf/",
             "cefr": "A2", "sceneTags": ["bakery"], "carrier": "object",
             "slotCategories": ["food"]},
            {"lemma": "order", "pos": "v", "senses": ["点（餐）", "订购"], "ipa": "/ˈɔːrdər/",
             "cefr": "A2", "sceneTags": ["bakery", "cafe"], "carrier": "phrase",
             "slotCategories": []},
        ],
    }
    root = tmp_path / "assets"
    (root / "wordbook").mkdir(parents=True)
    (root / "wordbook" / "dictionary.json").write_text(json.dumps(d), encoding="utf-8")
    dic = Dictionary.load(root)
    entries = dic.get("loaf", "n")
    assert len(entries) == 1
    assert entries[0].carrier == "object"
    assert entries[0].slot_categories == ["food"]
    assert entries[0].cefr == "A2"
    assert [e.pos for e in dic.get("order")] == ["v"]          # 未指定 pos
    assert len(dic.get("order")[0].senses) == 2                # 多 sense 全保留
    assert len(dic.all()) == 2
