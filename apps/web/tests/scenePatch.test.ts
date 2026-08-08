import { describe, expect, it } from 'vitest';
import { applyScenePatch } from '../src/scenePatch';
import type { Entity } from '../src/types';

const loaf: Entity = { id: 'counter.main-1', component: 'prop', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'food.loaf' }, semantics: { name: 'loaf', wordId: 'word_loaf_n_1' }, interactions: ['ask'] };
const apple: Entity = { id: 'counter.main-1', component: 'prop', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'food.apple' }, semantics: { name: 'apple', wordId: 'word_apple_n_1' }, interactions: ['ask'] };
const extra: Entity = { id: 'shelf.top-2', component: 'prop', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'food.loaf' }, semantics: { name: 'loaf' }, interactions: ['ask'] };

describe('applyScenePatch', () => {
  it('replace upserts by id and keeps others', () => {
    const r = applyScenePatch([loaf], { displayName: 'X', time: 'day' }, [{ op: 'replace', path: '/entities/counter.main-1', entity: apple }]);
    expect(r.entities.map((e) => e.semantics.wordId)).toEqual(['word_apple_n_1']);
  });
  it('add appends, remove drops', () => {
    let r = applyScenePatch([loaf], { displayName: 'X', time: 'day' }, [{ op: 'add', path: '/entities/shelf.top-2', entity: extra }]);
    expect(r.entities.length).toBe(2);
    r = applyScenePatch(r.entities, r.setting, [{ op: 'remove', path: '/entities/shelf.top-2' }]);
    expect(r.entities.length).toBe(1);
  });
  it('setting replace applies; unknown path ignored', () => {
    const r = applyScenePatch([loaf], { displayName: 'X', time: 'day' }, [
      { op: 'replace', path: '/setting', value: { displayName: 'Y', time: 'morning' } },
      { op: 'add', path: '/schemaVersion', value: 'hack' },
    ]);
    expect(r.setting.displayName).toBe('Y');
    expect(r.entities.length).toBe(1);
  });
});
