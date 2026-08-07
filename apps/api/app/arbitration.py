"""回合仲裁状态：同时仅 1 个主动说话人；pendingSpeakers 留 schema、v1 不排队。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ArbitrationState:
    active_speaker: str | None = None
    conversation_focus: str | None = None
    focus_source: str | None = None
    focus_expires_ms: int | None = None
    pending_speakers: list[str] = field(default_factory=list)

    def reset(self, default_npc_id: str | None) -> None:
        self.active_speaker = f"npc:{default_npc_id}" if default_npc_id else None
        self.conversation_focus = self.active_speaker
        self.focus_source = "scene_default"
        self.focus_expires_ms = None
        self.pending_speakers = []

    def set_focus(self, npc_id: str, source: str) -> str:
        self.active_speaker = f"npc:{npc_id}"
        self.conversation_focus = self.active_speaker
        self.focus_source = source
        self.focus_expires_ms = int(time.time() * 1000) + 60_000
        return self.active_speaker
