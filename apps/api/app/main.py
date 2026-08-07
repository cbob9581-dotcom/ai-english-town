from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.event_store import EventStore
from app.llm.client import get_client
from app.llm.npc_actor import NpcActor
from app.llm.tutor_cache import TutorCache
from app.llm_log import LlmLog
from app.scene_store import SceneStore
from app.scripted_npc import reply as scripted_reply
from app.settings import Settings
from app.workers import asr_client as worker_asr, tts_client as worker_tts

SCENE_ID = "scene_bakery_001"
ASR_URL = "http://127.0.0.1:8001"
TTS_BASE = "http://127.0.0.1:8002"


def create_app(events: EventStore | None = None, settings: Settings | None = None, *,
               asr_client=None, tts_client=None, llm_client=None) -> FastAPI:
    settings = settings or Settings.from_env()
    events = events or EventStore(settings.db_path)
    scenes = SceneStore(settings.asset_root)
    scene = scenes.get_compiled_scene(SCENE_ID)
    scene_words = {
        e["semantics"]["wordId"]: e["semantics"]["name"]
        for e in scene["entities"] if e.get("semantics", {}).get("wordId")
    }
    entity_words = {
        e["id"]: (e["semantics"]["wordId"], e["semantics"]["name"])
        for e in scene["entities"] if e.get("semantics", {}).get("wordId")
    }
    llm_log = LlmLog(events.connection)
    cache = TutorCache(events.connection, settings.tutor_cache_dir)
    client = llm_client or get_client(settings)
    actor = NpcActor(client, settings, llm_log, scene_words,
                     lambda u: scripted_reply(u)["speech"])

    app = FastAPI(title="english-town-api", version="0.2.0")
    app.state.events = events
    app.state.settings = settings
    app.state.scenes = scenes
    app.state.scene_words = scene_words
    app.state.entity_words = entity_words
    app.state.llm_log = llm_log
    app.state.tutor_cache = cache
    app.state.actor = actor
    app.state.asr_client = asr_client or (lambda audio: worker_asr(audio, f"{ASR_URL}/transcribe"))
    app.state.tts_client = tts_client or (lambda text: worker_tts(text, TTS_BASE))
    app.state.sessions = {}

    from app.ws import router as ws_router
    app.include_router(ws_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "archetypeCount": len(scenes.list_archetype_ids())}

    @app.get("/api/archetypes")
    def archetypes() -> dict:
        return {"ids": scenes.list_archetype_ids()}

    @app.get("/api/scenes/{scene_id}")
    def scene(scene_id: str) -> dict:
        try:
            return scenes.get_compiled_scene(scene_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown scene: {scene_id}")

    return app


app = create_app()
