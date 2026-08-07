from app.event_store import EventStore
from app.llm.npc_actor import build_history


def test_build_history_pairs_and_interruption(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    events.append("s1", "dialogue.turn", {"turnId": "t1", "utteranceId": "u1", "userText": "hello",
                                          "npcText": "Hi there.", "audioBytes": 10})
    events.append("s1", "dialogue.turn", {"turnId": "t2", "utteranceId": "u2", "userText": "loaf",
                                          "npcText": "A loaf!", "audioBytes": 10})
    events.append("s1", "dialogue.turn.interrupted", {"generationId": "g1", "turnId": "t2", "playedMs": 1840})
    hist = build_history(events, "s1")
    assert hist == [
        {"speaker": "learner", "text": "hello"},
        {"speaker": "rosa", "text": "Hi there."},
        {"speaker": "learner", "text": "loaf"},
        {"speaker": "rosa", "text": "A loaf!", "interrupted_after_ms": 1840},
    ]


def test_build_history_keeps_pairs_and_newest(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    for i in range(4):
        events.append("s1", "dialogue.turn",
                      {"turnId": f"t{i}", "utteranceId": f"u{i}", "userText": f"hello {i}",
                       "npcText": f"Hi from t{i}.", "audioBytes": 1})
    assert len(build_history(events, "s1")) == 8
    tight = build_history(events, "s1", limit=10, max_chars=10)
    assert len(tight) == 2          # 预算太小 → 只保留最新一对
    assert tight[0] == {"speaker": "learner", "text": "hello 3"}
    assert tight[1] == {"speaker": "rosa", "text": "Hi from t3."}


def test_build_history_limit_drops_oldest_pairs(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    for i in range(12):
        events.append("s1", "dialogue.turn",
                      {"turnId": f"t{i}", "utteranceId": f"u{i}", "userText": f"hello {i}",
                       "npcText": f"Hi from t{i}.", "audioBytes": 1})
    hist = build_history(events, "s1", limit=10, max_chars=99999)
    # 只保留最近 10 轮（20 条），从旧丢弃
    assert len(hist) == 20
    assert hist[0]["text"] == "hello 2"
    assert hist[1]["text"] == "Hi from t2."
