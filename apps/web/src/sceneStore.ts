// apps/web/src/sceneStore.ts
import { create } from 'zustand';
import type { Entity } from './types';
import { applyScenePatch, type PatchOp } from './scenePatch';

export type SceneStatus = 'idle' | 'skeleton' | 'filled' | 'degraded';

interface SceneState {
  sceneId: string | null;
  generationId: string | null;
  archetypeId: string | null;
  revision: number;
  status: SceneStatus;
  setting: { displayName: string; time: string } | null;
  background: { style: string; gradient: string; decor: string[]; ambienceKey: string } | null;
  entities: Entity[];
  characters: { slotId: string; npcId: string; name?: string; voice?: string }[];
  exits: { id: string; targetArchetypeId?: string }[];
  activeSpeaker: string | null;
  applySkeleton: (m: any) => void;
  applyPatch: (m: any) => void;
  applyDegraded: (m: any) => void;
  applyFocus: (m: any) => void;
  reset: () => void;
}

export const useSceneStore = create<SceneState>((set) => ({
  sceneId: null, generationId: null, archetypeId: null, revision: 0,
  status: 'idle', setting: null, background: null, entities: [], characters: [], exits: [],
  activeSpeaker: null,

  applySkeleton: (m) => set({
    sceneId: m.sceneId, generationId: m.generationId, archetypeId: m.archetypeId,
    revision: m.revision ?? 1, status: 'skeleton',
    setting: m.setting ?? null, background: m.background ?? null,
    entities: m.entities ?? [], characters: m.characters ?? [], exits: m.exits ?? [],
  }),
  applyPatch: (m) => set((s) => {
    const r = applyScenePatch(s.entities, s.setting, (m.ops ?? []) as PatchOp[]);
    return { entities: r.entities, setting: r.setting, status: 'filled', revision: (m.baseRevision ?? 0) + 1 };
  }),
  applyDegraded: () => set({ status: 'degraded' }),
  applyFocus: (m) => set({ activeSpeaker: m.activeSpeaker ?? null }),
  reset: () => set({ sceneId: null, generationId: null, archetypeId: null, revision: 0, status: 'idle', setting: null, background: null, entities: [], characters: [], exits: [], activeSpeaker: null }),
}));
