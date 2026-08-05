from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.event_store import EventStore
from app.scene_store import SceneStore
from app.settings import Settings


def create_app(events: EventStore | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    events = events or EventStore(settings.db_path)
    scenes = SceneStore(settings.asset_root)

    app = FastAPI(title="english-town-api", version="0.1.0")
    app.state.events = events
    app.state.settings = settings

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
