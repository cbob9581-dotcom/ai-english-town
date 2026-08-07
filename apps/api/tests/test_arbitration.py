from app.arbitration import ArbitrationState


def test_reset_to_scene_default() -> None:
    a = ArbitrationState()
    a.set_focus("npc_rosa", "user_click")
    a.reset("npc_tom")
    assert a.active_speaker == "npc:npc_tom"
    assert a.conversation_focus == "npc:npc_tom"
    assert a.pending_speakers == []


def test_set_focus_switches_and_sets_expiry() -> None:
    a = ArbitrationState()
    speaker = a.set_focus("npc_rosa", "user_click")
    assert speaker == "npc:npc_rosa"
    assert a.focus_source == "user_click"
    assert a.focus_expires_ms is not None
