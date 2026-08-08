import asyncio, json
from datetime import datetime, timezone
from app.event_store import EventStore
from app.learning.memory import MemoryStore
from app.learning.store import LearningStore
from app.settings import Settings

_WORLD = {"memoryPolicyVersion": "v1",
          "scenes": {"plaza": {"count": 3, "lastAt": None}, "bakery": {"count": 2, "lastAt": None}},
          "wordMastery": {"known": 8, "learning": 5, "review": 0, "reviewDue": 3,
                          "weakWords": [{"wordId": "word_shelf_n_1", "lemma": "shelf"}]},
          "userProfile": {"productiveAvg": 0.7, "receptiveAvg": 0.8, "asrWordConfAvg": 0.0,
                          "helpCount": 4, "commonErrorWords": []},
          "askedWords": [{"lemma": "loaf"}]}

def test_director_build_messages_embeds_world_summary():
    from app.llm.scene_director import LlmSceneDirector
    class _NoSlots:
        def concepts_in(self, cat): raise AssertionError("no slots")
        def npcs_in(self, role): raise AssertionError("no slots")
    d = LlmSceneDirector(object(), Settings(), None)
    arch = {"archetypeId": "bakery", "displayName": "Bakery", "propSlots": [], "npcSlots": []}
    messages = d._build_messages(arch, _NoSlots(), ["plaza"], world_summary=_WORLD)
    payload = json.loads(messages[1]["content"])
    assert payload["recentScenes"] == ["plaza"]
    assert "User memory summary:" in payload["worldSummary"]
    assert "Known words: 8, learning: 5, review due: 3" in payload["worldSummary"]

def test_tutor_reply_injects_world_summary(tmp_path):
    from types import SimpleNamespace
    from app.llm.tutor import CompanionTutor
    captured = {}
    class FakeLog:
        def record(self, **k): pass
    class FakeClient:
        async def complete_json(self, messages, **k):
            captured["user"] = messages[1]["content"]
            return SimpleNamespace(json={"word": "loaf", "scaffold": "A loaf is bread."}, usage=None)
    class FakeCache:
        def get(self, wid): return None
        def audio_path(self, wid): return str(tmp_path / f"{wid}.mp3")
        def put(self, *a): pass
    async def tts(text): return {"audioBase64": "AA==", "sampleRate": 24000}
    t = CompanionTutor(FakeClient(), Settings(), FakeLog(), FakeCache(), tts)
    asyncio.run(t.reply(session_id="s1", generation_id="g1", word_id="w1", word="loaf",
                        world_summary=_WORLD))
    assert "User memory summary:" in captured["user"]
    assert "Visited scenes: plaza × 3, bakery × 2" in captured["user"]

def test_prefetch_cache_key_includes_revision(tmp_path):
    from app.scene_prefetch import ScenePrefetchCache
    c = ScenePrefetchCache(ttl_s=60)
    c.put("bakery", 0, {"v": 1})
    assert c.get("bakery", 0) == {"v": 1}
    assert c.get("bakery", 1) is None          # revision 变化 → 未命中（需重查 Director）

def test_prefetch_hit_when_only_scene_count_changes(tmp_path):
    from app.scene_prefetch import ScenePrefetchCache
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s1", "generationId": "g1", "revision": 1, "source": "connect"})
    with events.write_lock:
        mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now)
        conn.commit()
    assert mem.get_revision("local") == 1
    cache = ScenePrefetchCache()
    cache.put("plaza", mem.get_revision("local"), {"v": 1})
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s2", "generationId": "g2", "revision": 1, "source": "exit"})
    with events.write_lock:
        mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now)
        conn.commit()
    assert mem.get_revision("local") == 1      # 场景计数变但非新场景 → revision 不变（spec §10b）
    assert cache.get("plaza", mem.get_revision("local")) == {"v": 1}   # 缓存仍命中
