from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.event_store import EventStore
from app.llm.client import get_client
from app.llm.npc_actor import NpcActor
from app.llm.tutor import CompanionTutor
from app.llm.tutor_cache import TutorCache
from app.llm_log import LlmLog
from app.scene_store import SceneStore
from app.scripted_npc import reply as scripted_reply
from app.settings import Settings
from app.workers import asr_client as worker_asr, tts_client as worker_tts

ASR_URL = "http://127.0.0.1:8001"
TTS_BASE = "http://127.0.0.1:8002"


def create_app(events: EventStore | None = None, settings: Settings | None = None, *,
               asr_client=None, tts_client=None, llm_client=None) -> FastAPI:
    settings = settings or Settings.from_env()
    events = events or EventStore(settings.db_path)
    scenes = SceneStore(settings.asset_root)
    catalog = scenes.catalog
    llm_log = LlmLog(events.connection)
    cache = TutorCache(events.connection, settings.tutor_cache_dir)
    client = llm_client or get_client(settings)
    asr_impl = asr_client or (lambda audio: worker_asr(audio, f"{ASR_URL}/transcribe"))
    tts_impl = tts_client or (lambda text: worker_tts(text, TTS_BASE))

    def scene_factory(scene_words: dict, entity_by_word_id: dict, npc_id: str | None = None) -> NpcActor:
        persona = (
            "You are a friendly helper in a small English town. Reply in short, simple English "
            "suitable for an A1-A2 learner. Never mention that you are an AI. Use only plain "
            "English text: no newlines, no URLs, no code."
        )
        if npc_id:
            npc = catalog.npc(npc_id)
            if npc:
                persona = npc.persona
        return NpcActor(client, settings, llm_log, scene_words,
                        lambda u: scripted_reply(u)["speech"], persona=persona)

    actor = scene_factory({}, {})
    tutor = CompanionTutor(client, settings, llm_log, cache, tts_impl)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="english-town-api", version="0.2.0", lifespan=lifespan)
    app.state.events = events
    app.state.settings = settings
    app.state.scenes = scenes
    app.state.catalog = catalog
    app.state.scene_factory = scene_factory
    app.state.llm_log = llm_log
    app.state.tutor_cache = cache
    app.state.actor = actor
    app.state.tutor = tutor
    app.state.asr_client = asr_impl
    app.state.tts_client = tts_impl
    app.state.sessions = {}

    from app.ws import router as ws_router
    app.include_router(ws_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "archetypeCount": len(scenes.list_archetype_ids())}

    @app.get("/api/archetypes")
    def archetypes() -> dict:
        return {"ids": scenes.list_archetype_ids()}

    @app.get("/api/dev/archetypes")
    def dev_archetypes() -> list[dict]:
        out = []
        for aid in sorted(scenes.list_archetype_ids()):
            arche = scenes.get_archetype(aid)
            skeleton = scenes.compile_skeleton(aid, scene_id=f"dev_{aid}", seed=f"dev_{aid}",
                                               generation_id="dev")
            out.append({"archetypeId": aid, "displayName": arche["displayName"],
                        "skeleton": skeleton, "zones": arche["zones"],
                        "propSlots": arche["propSlots"], "npcSlots": arche["npcSlots"],
                        "exits": arche["exits"]})
        return out

    @app.get("/api/scenes/{scene_id}")
    def scene(scene_id: str) -> dict:
        try:
            return scenes.get_compiled_scene(scene_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown scene: {scene_id}")

    return app


app = create_app()
