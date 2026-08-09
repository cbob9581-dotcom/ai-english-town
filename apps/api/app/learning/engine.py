"""学习引擎：单一事务证据记录 + outbox 兜底 + 选词 + 回合证据归因。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.learning import scheduler as sched_mod
from app.learning.evidence import apply_evidence, classify_round
from app.learning.memory import MemoryStore
from app.learning.scores import normalize_asr_confidence
from app.learning.store import LearningStore


class LearningEngine:
    def __init__(self, store: LearningStore, events, settings) -> None:
        self.store = store
        self.events = events
        self.settings = settings
        self.memory = MemoryStore(store.conn)
        # 并发由 events.write_lock 提供（sequence 分配与提交同锁）

    # ---- 选词 ----
    def pick_scene_words(self, archetype_id: str, archetype: dict, now: datetime) -> dict[str, str]:
        words = self.store.list_items_by_scene("local", archetype_id)
        slots = sched_mod.scene_prop_slot_categories(archetype)
        chosen = sched_mod.pick(words, archetype_id=archetype_id, now=now,
                                slot_categories=slots, limit=7)
        out: dict[str, str] = {}
        for w in words:
            if w["word_id"] in chosen:
                out[w["word_id"]] = w["lemma"]
        return out

    # ---- 证据 ----
    def record_evidence(self, session_id: str, evidence: dict,
                        *, event_id: str | None = None) -> int | None:
        """单事务：session_events(evidence, internal) + evidence_events + mastery_states。
        复用 event_store 的锁与连接（sequence 分配同锁）。失败 → outbox，不阻断回合。
        event_id 级去重：同 event_id 重复提交 → 直接返回既有 seq，不 append、不 apply_evidence
        （兑现「不重复计算」约束；锁内单写者，无 TOCTOU）。"""
        with self.events.write_lock:
            conn = self.events.connection
            try:
                if event_id is not None:
                    existing = conn.execute(
                        "SELECT sequence FROM session_events WHERE event_id = ?",
                        (event_id,)).fetchone()
                    if existing is not None:
                        return existing[0]      # 重复提交：不重复计算
                seq = self.events.append_in_tx(conn, session_id, "evidence",
                                               _internal_payload(evidence),
                                               event_id=event_id, internal=True)
                _now = datetime.fromisoformat(evidence["created_at"])
                apply_evidence(self.store, "local", evidence, now=_now)
                try:
                    self.memory.apply_memory_updates(conn, self.events, "local",
                                                     evidence=evidence, now=_now)
                except Exception as e:  # noqa: BLE001 —— 记忆抽取失败不杀证据事务、revision 不推进
                    print(f"memory update failed: {e}", flush=True)
                conn.commit()
                return seq
            except Exception as e:  # noqa: BLE001 —— 证据失败不杀回合；入 outbox 下次补
                conn.rollback()
                print(f"evidence dropped to outbox: {e!r}", flush=True)
                try:
                    self.store.outbox_push("local", json.dumps(evidence, ensure_ascii=False))
                except Exception:  # noqa: BLE001 —— outbox 也失败则只丢日志
                    pass
                return None

    def drain_outbox(self) -> int:
        rows = self.store.outbox_drain("local")
        n = 0
        for payload in rows:
            ev = json.loads(payload)
            seq = self.record_evidence(ev.get("session_id", "s1"), ev,
                                       event_id=ev.get("evidence_id"))
            if seq is not None:
                n += 1
        return n

    def record_round(self, session_id: str, scene_words: dict, npc_text: str,
                     user_text: str, confidence: float, *, turn_id: str,
                     target_word_ids: set[str], attempt_id: str | None = None,
                     words: list[dict] | None = None) -> int:
        conf = normalize_asr_confidence(confidence) if confidence < 0 else confidence
        drafts = classify_round(scene_words, npc_text, user_text, conf,
                                target_word_ids=target_word_ids)
        for d in drafts:
            now = datetime.now(timezone.utc)
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "event_seq": 0, "session_id": session_id,
                "attempt_id": attempt_id or f"attempt_{turn_id}",
                "turn_id": turn_id,
                "objective_id": f"obj_scene_{next(iter(target_word_ids))}" if target_word_ids else None,
                "word_id": d["word_id"], "source": d["source"],
                "prompt_level": d["prompt_level"], "axis": d["axis"],
                "result": d["result"], "confidence": d["confidence"],
                "evidence_policy_version": self.settings.evidence_policy_version,
                "fsrs_algorithm_version": self.settings.fsrs_algorithm_version,
                "created_at": now.isoformat(),
            }
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])
        if words is not None:
            self._record_word_production(session_id, scene_words, user_text, words,
                                         drafts, turn_id, attempt_id)
        return len(drafts)

    def _record_word_production(self, session_id: str, scene_words: dict, user_text: str,
                                words: list[dict], drafts: list[dict], turn_id: str,
                                attempt_id: str | None) -> None:
        from app.learning.word_confidence import score_word_confidence
        draft_ids = {d["word_id"] for d in drafts}
        now = datetime.now(timezone.utc)
        for wid, score in score_word_confidence(words, scene_words, user_text).items():
            if wid not in draft_ids:
                continue
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "event_seq": 0, "session_id": session_id,
                "attempt_id": attempt_id or f"attempt_{turn_id}",
                "turn_id": turn_id,
                "objective_id": f"obj_scene_{wid}",
                "word_id": wid, "source": "word_production", "prompt_level": 0,
                "axis": "asr_word_confidence", "result": "success", "confidence": score,
                "evidence_policy_version": self.settings.evidence_policy_version,
                "fsrs_algorithm_version": self.settings.fsrs_algorithm_version,
                "created_at": now.isoformat(),
            }
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])


def _internal_payload(evidence: dict) -> dict:
    return {"evidenceId": evidence["evidence_id"], "wordId": evidence["word_id"],
            "source": evidence["source"], "result": evidence["result"],
            "internal": True, "objectiveId": evidence.get("objective_id")}
