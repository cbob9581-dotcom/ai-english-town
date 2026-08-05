from pydantic import BaseModel, Field


class Layout(BaseModel):
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=0, le=1000)
    h: int = Field(ge=0, le=1000)
    anchor: str


class Entity(BaseModel):
    id: str
    component: str
    layout: Layout
    appearance: dict
    semantics: dict
    interactions: list[str] = Field(default_factory=list)


class Background(BaseModel):
    style: str
    gradient: str
    decor: list[str]
    ambienceKey: str


class Zone(BaseModel):
    x: tuple[int, int]
    y: tuple[int, int]
    anchor: str


class Archetype(BaseModel):
    archetypeId: str
    displayName: str
    background: Background
    zones: dict[str, Zone]
    propSlots: list[dict]
    npcSlots: list[dict]
    exits: list[dict]


class ScenePlan(BaseModel):
    schemaVersion: str
    sceneId: str
    generationId: str
    revision: int
    mode: str
    archetypeId: str
    setting: dict
    fills: list[dict]
    characters: list[dict]
    objectives: list = Field(default_factory=list)
    exits: list = Field(default_factory=list)
