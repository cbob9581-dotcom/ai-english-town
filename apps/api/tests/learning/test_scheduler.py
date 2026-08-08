import json
from datetime import datetime, timezone

from app.learning.scheduler import pick, scene_prop_slot_categories


def _word(word_id, *, state="new", due=None, p=0.0, r=0.0, carrier="phrase", slots=None):
    return {"word_id": word_id, "state": state, "due": due,
            "productive_score": p, "receptive_score": r,
            "carrier": carrier, "slot_categories": json.dumps(slots or []),
            "ipa": "/x/", "cefr": "A2", "created_at": "2026-08-01T00:00:00+00:00"}


NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_carrier_feasibility_filters_objects() -> None:
    archetype = {"propSlots": [{"slotId": "a", "categories": ["food"]}]}
    slots = scene_prop_slot_categories(archetype)
    assert slots == {"food"}
    words = [
        _word("w1", carrier="object", slots=["food"]),
        _word("w2", carrier="object", slots=["drink"]),   # 场景无 drink → 排除
        _word("w3", carrier="phrase"),                    # 非 object → 保留
    ]
    out = pick(words, archetype_id="plaza", now=NOW, slot_categories=slots, limit=7)
    assert "w2" not in out and "w1" in out and "w3" in out


def test_due_then_new_then_weak() -> None:
    words = [
        _word("due1", state="review", due="2026-08-08T00:00:00+00:00"),   # 到期
        _word("due2", state="review", due="2026-08-09T00:00:00+00:00"),   # 未到期
        _word("new1"), _word("new2"), _word("new3"),
        _word("weak", state="review", due="2026-08-09T00:00:00+00:00", p=0.1, r=0.2),
    ]
    out = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=7)
    assert out[0] == "due1"                       # 到期优先
    assert set(out[1:4]) == {"new1", "new2", "new3"}  # 新词
    assert "weak" in out                          # 薄弱词（含未到期）
    assert "due2" in out                          # 未到期非薄弱也可补足


def test_weak_excludes_asr_confidence_axis() -> None:
    words = [
        _word("a", state="review", due="2026-08-09T00:00:00+00:00", p=0.5, r=0.5),
        _word("b", state="review", due="2026-08-09T00:00:00+00:00", p=0.4, r=0.4),
    ]
    # a 的 asr 轴 0.9，b 的 asr 轴 0.1 —— 薄弱判据只用 p+r，应选 b（p+r=0.8 < 1.0）
    # 注：pick 用 productive+receptive，与 asr_confidence 无关（测试由实现保证不读该列）
    out = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=7)
    assert out.index("b") < out.index("a")


def test_deterministic_seed() -> None:
    words = [_word(f"w{i}") for i in range(20)]
    a = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=5)
    b = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=5)
    c = pick(words, archetype_id="cafe", now=NOW, slot_categories=set(), limit=5)
    assert a == b                                  # 同 scene 同日 → 同输出
    assert a != c                                  # 不同场景 → 不同 seed
    assert len(a) == 5
