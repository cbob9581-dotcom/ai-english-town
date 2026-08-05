import { describe, it, expect } from 'vitest';
import { ScenePlanSchema } from '../src/index';
import bakery from './fixtures/bakery-plan.json';

describe('scene-schema', () => {
  it('accepts the bakery fixture as a valid ScenePlan', () => {
    const plan = ScenePlanSchema.parse(bakery);
    expect(plan.archetypeId).toBe('bakery');
    expect(plan.fills.length).toBe(3);
  });

  it('rejects a plan with an invalid component', () => {
    const bad = { ...bakery, fills: [{ slotId: 'counter.main', entity: { ...bakery.fills[0].entity, component: 'script' } }] };
    expect(() => ScenePlanSchema.parse(bad)).toThrow();
  });
});
