"""学习引擎 HTTP 端点（挂 main.py）。写侧走引擎单写锁。错误统一 {detail}。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.learning.encounters import list_spontaneous, promote
from app.learning.wordbook import ImportValidationError, run_import
from app.llm.concepts import invalidate_word_id_cache

router = APIRouter(prefix="/api")


def _eng(request: Request):
    return request.app.state.learning


def _dict(request: Request):
    return request.app.state.dictionary


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/word-lists/import")
def import_words(req: Request, body: dict):
    eng = _eng(req)
    try:
        res = run_import(eng.store, _dict(req), "local", body.get("words", []),
                         name=body.get("name"), now=_now())
    except ImportValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    invalidate_word_id_cache()
    return res


@router.get("/word-lists/spontaneous")
def list_spont(req: Request):
    return {"items": list_spontaneous(_eng(req).store, "local")}


@router.post("/word-lists/spontaneous/import")
def promote_spont(req: Request, body: dict):
    lemmas = body.get("lemmas", [])
    n = promote(_eng(req).store, "local", lemmas, now=_now())
    invalidate_word_id_cache()
    return {"promoted": n, "unknown": max(0, len(lemmas) - n)}


@router.get("/progress/summary")
def summary(req: Request):
    eng = _eng(req)
    rows = eng.store.all_words("local")
    now = _now()
    due = [r for r in rows if r["state"] != "new" and r["due"] and r["due"] <= now.isoformat()]
    quest = sum(1 for r in rows if r["source"] == "quest")
    free = sum(1 for r in rows if r["source"] == "free")
    page = max(1, int(req.query_params.get("page", 1)))
    page_size = min(100, max(1, int(req.query_params.get("page_size", 50))))
    start = (page - 1) * page_size
    return {
        "strategy": {"evidencePolicyVersion": eng.settings.evidence_policy_version,
                     "fsrsAlgorithmVersion": eng.settings.fsrs_algorithm_version},
        "totals": {"quest": quest, "free": free, "dueToday": len(due)},
        "page": page, "pageSize": page_size, "totalWords": len(rows),
        "words": [_word_summary(req, r) for r in rows[start:start + page_size]],
    }


@router.get("/progress/words/{word_id}/evidence")
def word_evidence(req: Request, word_id: str):
    items = _eng(req).store.evidence_for_word("local", word_id)
    return {"items": items}


def _word_summary(req: Request, r: dict) -> dict:
    import json as _json
    return {"wordId": r["word_id"], "lemma": r["lemma"], "pos": r["pos"], "ipa": r["ipa"],
            "cefr": r["cefr"], "sceneTags": _json.loads(r["scene_tags"]), "source": r["source"],
            "carrier": r["carrier"],
            "scores": {"productive": r["productive_score"], "receptive": r["receptive_score"],
                       "asrConfidence": r["asr_confidence_score"]},
            "fsrs": {"state": r["state"], "due": r["due"], "reps": r["reps"], "lapses": r["lapses"]},
            "evidenceCount": len(_eng(req).store.evidence_for_word("local", r["word_id"])),
            "lastEvidenceAt": None}
