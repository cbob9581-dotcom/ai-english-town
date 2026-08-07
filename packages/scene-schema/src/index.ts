import { z } from 'zod';

export const EntityKindSchema = z.enum([
  'image', 'label', 'npc', 'companion', 'prop', 'door', 'dialogue-zone', 'ambient-audio',
]);

export const LayoutSchema = z.object({
  x: z.number().int().min(0).max(1000),
  y: z.number().int().min(0).max(1000),
  w: z.number().int().min(0).max(1000),
  h: z.number().int().min(0).max(1000),
  anchor: z.enum(['bottom', 'center', 'top']),
});

export const EntitySchema = z.object({
  id: z.string(),
  component: EntityKindSchema,
  layout: LayoutSchema,
  appearance: z.object({ visualKey: z.string().regex(/^[a-z0-9]+\.[a-z0-9]+$/) }),
  semantics: z.object({
    name: z.string(),
    wordId: z.string().optional(),
    description: z.string().optional(),
  }),
  interactions: z.array(z.enum(['focus', 'ask', 'inspect', 'pick'])).optional(),
});

export const ArchetypeSchema = z.object({
  archetypeId: z.string(),
  displayName: z.string(),
  background: z.object({
    style: z.literal('gradient'),
    gradient: z.string(),
    decor: z.array(z.string()),
    ambienceKey: z.string(),
  }),
  zones: z.record(z.object({
    x: z.tuple([z.number().int(), z.number().int()]),
    y: z.tuple([z.number().int(), z.number().int()]),
    anchor: z.enum(['bottom', 'center', 'top']),
  })),
  propSlots: z.array(z.object({ slotId: z.string(), zone: z.string(), categories: z.array(z.string()) })),
  npcSlots: z.array(z.object({ slotId: z.string(), zone: z.string(), role: z.string() })),
  exits: z.array(z.object({ direction: z.enum(['left', 'right', 'up', 'down']), targetKind: z.string() })),
});

export const ScenePlanSchema = z.object({
  schemaVersion: z.literal('1.0'),
  sceneId: z.string(),
  generationId: z.string(),
  revision: z.number().int().min(1),
  mode: z.enum(['free', 'quest']),
  archetypeId: z.string(),
  setting: z.object({ displayName: z.string(), time: z.string() }),
  fills: z.array(z.object({ slotId: z.string(), entity: EntitySchema })),
  characters: z.array(z.object({ slotId: z.string(), npcId: z.string() })),
  objectives: z.array(z.unknown()).default([]),
  exits: z.array(z.unknown()).default([]),
});

export type EntityKind = z.infer<typeof EntityKindSchema>;
export type Entity = z.infer<typeof EntitySchema>;
export type Archetype = z.infer<typeof ArchetypeSchema>;
export type ScenePlan = z.infer<typeof ScenePlanSchema>;
