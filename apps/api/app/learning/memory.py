"""WorldMemory 结构化抽取：物化单例快照 + 触发式重建（apply_memory_updates）。
build_world_summary 是唯一事实源（纯读库重建，跨会话）；revision 只随实质变化 +1
（新场景首次进入 / 新词进入 known/learning/review / 新求助词）。"""
from __future__ import annotations

import json
from datetime import datetime


class MemoryCache:
    """进程内只读快照缓存：known/learning/review 词集 + 求助词集 + 已访问场景集。
    决定 should_touch；_store_summary 后 refresh() 使同轮后续证据不重复 +revision。"""
    def __init__(self, conn) -> None:
        self.conn = conn
        self.refresh()

    def refresh(self) -> None:
        rows = self.conn.execute(
            "SELECT word_id FROM mastery_states WHERE user_id='local' AND state IN ('learning','review','relearning')").fetchall()
        self.known_word_ids: set[str] = {r[0] for r in rows}
        helped: set[tuple[str, str]] = set()
        for r in self.conn.execute(
                "SELECT li.lemma, COALESCE(li.pos,'') FROM evidence_events ev "
                "JOIN learning_items li ON li.word_id=ev.word_id AND li.user_id=ev.user_id "
                "WHERE ev.user_id='local' AND ev.source='help'"):
            helped.add((r[0], r[1]))
        for r in self.conn.execute(
                "SELECT lemma, COALESCE(pos,'') FROM spontaneous_words WHERE user_id='local' AND asked=1"):
            helped.add((r[0], r[1]))
        self.helped = helped
        row = self.conn.execute(
            "SELECT world_summary_json FROM memory_state WHERE user_id='local'").fetchone()
        self.visited_archetypes: set[str] = set(json.loads(row[0]).get("scenes", {})) if row else set()


class MemoryStore:
    def __init__(self, conn) -> None:
        self.conn = conn
        self.cache = MemoryCache(conn)

    def get_world_summary(self, user_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT world_summary_json FROM memory_state WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_revision(self, user_id: str) -> int:
        row = self.conn.execute("SELECT revision FROM memory_state WHERE user_id=?", (user_id,)).fetchone()
        return int(row[0]) if row else 0

    def _store_summary(self, conn, user_id: str, summary: dict, now: datetime,
                       events, reason: str, session_id: str = "s1") -> None:
        """调用方单事务内：UPDATE memory_state + revision+1 + snapshot 事件。不 commit。"""
        summary["updatedAt"] = now.isoformat()
        summary["memoryPolicyVersion"] = summary.get("memoryPolicyVersion", "v1")
        revision = self.get_revision(user_id) + 1
        conn.execute(
            "INSERT INTO memory_state(user_id, world_summary_json, revision, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "world_summary_json=excluded.world_summary_json, revision=excluded.revision, updated_at=excluded.updated_at",
            (user_id, json.dumps(summary, ensure_ascii=False), revision, now.isoformat()))
        events.append_in_tx(conn, session_id, "world_summary.snapshot",
                            {"summary": summary, "revision": revision, "reason": reason},
                            event_id=f"mem_{revision}", internal=True)
        self.cache.refresh()

    def apply_memory_updates(self, conn, events, user_id: str, *,
                             scene_enter: str | None = None,
                             evidence: dict | None = None,
                             ask: dict | None = None,
                             now: datetime) -> bool:
        """should_touch → 重建 + _store_summary。不取锁、不 commit（调用方单事务/持 write_lock）。
        命中三种实质变化之一才 +revision：新场景首次进入 / 证据使词进 known-learning-review / 新求助词。"""
        touched = False
        reason = "memory"
        if scene_enter is not None:
            touched = scene_enter not in self.cache.visited_archetypes
            reason = f"scene:{scene_enter}"
        elif evidence is not None:
            wid = evidence["word_id"]
            if evidence["source"] == "help":
                row = conn.execute(
                    "SELECT lemma, COALESCE(pos,'') FROM learning_items WHERE user_id=? AND word_id=?",
                    (user_id, wid)).fetchone()
                touched = bool(row) and (row[0], row[1]) not in self.cache.helped
                reason = f"help:{wid}"
            else:
                row = conn.execute(
                    "SELECT state FROM mastery_states WHERE user_id=? AND word_id=?",
                    (user_id, wid)).fetchone()
                touched = (row is not None and row[0] in ("learning", "review", "relearning")
                           and wid not in self.cache.known_word_ids)
                reason = f"evidence:{wid}"
        elif ask is not None:
            key = (ask["lemma"], ask.get("pos") or "")
            touched = key not in self.cache.helped
            reason = f"ask:{key[0]}"
        if not touched:
            return False
        summary = build_world_summary(events, conn, user_id, now=now)
        # 无 evidence/ask（纯 scene_enter）时回退 "s1"（brief Step 4 注明）
        session_id = evidence["session_id"] if evidence is not None else (
            ask.get("session_id") or "s1" if ask is not None else "s1")
        self._store_summary(conn, user_id, summary, now, events, reason, session_id=session_id)
        return True


def build_world_summary(events, conn, user_id: str, *, now: datetime) -> dict:
    """纯读库重建（唯一事实源；snapshot 重放共用）。跨会话扫 session_events。"""
    scenes: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT payload_json FROM session_events WHERE event_type='scene.entered' ORDER BY sequence").fetchall()
    for r in rows:
        p = json.loads(r[0])
        a = p.get("archetypeId")
        cur = scenes.setdefault(a, {"count": 0, "lastAt": None})
        cur["count"] += 1
        cur["lastAt"] = p.get("generationId", "")
    mrows = conn.execute(
        "SELECT ms.word_id, li.lemma, ms.state, ms.due, ms.productive_score, ms.receptive_score "
        "FROM mastery_states ms JOIN learning_items li ON li.word_id=ms.word_id AND li.user_id=ms.user_id "
        "WHERE ms.user_id=?", (user_id,)).fetchall()
    states: dict[str, int] = {"new": 0, "learning": 0, "review": 0, "relearning": 0}
    due = 0
    weak = []
    for r in mrows:
        states[r["state"]] = states.get(r["state"], 0) + 1
        if r["state"] != "new" and r["due"] and r["due"] <= now.isoformat():
            due += 1
        if r["state"] != "new":
            weak.append({"wordId": r["word_id"], "lemma": r["lemma"],
                         "sum": (r["productive_score"] or 0.0) + (r["receptive_score"] or 0.0)})
    weak.sort(key=lambda w: w["sum"])
    # 平均分/求助仅统计已进入复习周期的词（state != 'new'），与 weakWords/reviewDue 口径一致
    prof = conn.execute(
        "SELECT AVG(productive_score) AS p, AVG(receptive_score) AS r, "
        "AVG(asr_word_confidence_score) AS awc, SUM(help_count) AS hc "
        "FROM mastery_states WHERE user_id=? AND state != 'new'", (user_id,)).fetchone()
    err_rows = conn.execute(
        "SELECT ev.word_id, li.lemma, COUNT(*) AS n FROM evidence_events ev "
        "JOIN learning_items li ON li.word_id=ev.word_id AND li.user_id=ev.user_id "
        "WHERE ev.user_id=? AND ev.source='error' GROUP BY ev.word_id ORDER BY n DESC LIMIT 3",
        (user_id,)).fetchall()
    asked = conn.execute(
        "SELECT lemma FROM spontaneous_words WHERE user_id=? AND asked=1", (user_id,)).fetchall()
    known = states["learning"] + states["review"] + states["relearning"]
    return {
        "memoryPolicyVersion": "v1",
        "scenes": {a: {"count": s["count"], "lastAt": s["lastAt"]} for a, s in scenes.items()},
        "wordMastery": {"known": known, "learning": states["learning"],
                        "review": states["review"], "reviewDue": due,
                        "weakWords": [{"wordId": w["wordId"], "lemma": w["lemma"]} for w in weak[:3]]},
        "userProfile": {"productiveAvg": round(prof["p"] or 0.0, 3),
                        "receptiveAvg": round(prof["r"] or 0.0, 3),
                        "asrWordConfAvg": round(prof["awc"] or 0.0, 3),
                        "helpCount": int(prof["hc"] or 0),
                        "commonErrorWords": [{"wordId": r[0], "lemma": r[1]} for r in err_rows]},
        "askedWords": [{"lemma": r[0]} for r in asked],
    }


def format_world_summary(summary: dict | None) -> str:
    """spec §4.5 最小模板（基线，字段语义不得偏离）。空摘要 → 空串（等价现状无块）。"""
    if not summary:
        return ""
    lines = ["User memory summary:"]
    scenes = summary.get("scenes", {})
    if scenes:
        lines.append("  Visited scenes: " + ", ".join(f"{a} × {s['count']}" for a, s in scenes.items()))
    wm = summary.get("wordMastery", {})
    lines.append(f"  Known words: {wm.get('known', 0)}, learning: {wm.get('learning', 0)}, "
                 f"review due: {wm.get('reviewDue', 0)}")
    weak = [w["lemma"] for w in wm.get("weakWords", [])]
    if weak:
        lines.append("  Top weak words: " + ", ".join(weak))
    asked = [w["lemma"] for w in summary.get("askedWords", [])]
    if asked:
        lines.append("  Words user explicitly asked about: " + ", ".join(asked))
    return "\n".join(lines)
