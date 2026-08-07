from pathlib import Path

from fastapi.testclient import TestClient

from app.event_store import EventStore
from app.main import create_app


def test_ws_mounted_and_control_appends_nothing_without_turn(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "e.db")
    app = create_app(events)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/sessions/sess-smoke") as ws:
            ws.send_json({"type": "playback.interrupted", "utteranceId": None})
    # 无活跃回合时，显式打断不写任何事件（phase-2 append-only：interrupted 需配对 dialogue.turn）
    assert events.list_after("sess-smoke", 0) == []
