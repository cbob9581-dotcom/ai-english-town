# apps/api/tests/test_memory_smoke.py（新建）
from app.event_store import EventStore
from app.learning.memory_smoke import main as smoke_main
from app.learning.store import LearningStore

def test_smoke_outputs_summary_and_bounded_growth(tmp_path):
    db = tmp_path / "e.db"
    events = EventStore(db)
    store = LearningStore(events.connection)
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s1", "generationId": "g1", "revision": 1, "source": "connect"})
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "s2", "generationId": "g2", "revision": 1, "source": "exit"})
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "s3", "generationId": "g3", "revision": 1, "source": "exit"})
    out = smoke_main(db)
    assert out["summary"]["scenes"]["bakery"]["count"] == 2
    assert out["revision"] == 2                       # plaza 新 + bakery 新 = 2（第 2 次 bakery 不变）
    assert out["revisionGrowth"]["maxStep"] == 1      # 单次最多 +1（不暴涨）
