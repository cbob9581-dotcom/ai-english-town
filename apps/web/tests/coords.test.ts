import { describe, it, expect } from 'vitest';
import { mapLogicalToCss, ensureMinHit } from '../src/coords';

describe('coords', () => {
  it('maps 0..1000 logical coords proportionally', () => {
    const r = mapLogicalToCss(250, 500, 100, 100, 1000, 600);
    expect(r).toEqual({ left: 250, top: 300, width: 100, height: 100 });
  });

  it('expands small hit areas around center to 44px', () => {
    const r = ensureMinHit({ left: 100, top: 100, width: 20, height: 20 });
    expect(r).toEqual({ left: 88, top: 88, width: 44, height: 44 });
  });

  it('leaves large areas untouched', () => {
    const r = ensureMinHit({ left: 0, top: 0, width: 120, height: 80 });
    expect(r).toEqual({ left: 0, top: 0, width: 120, height: 80 });
  });
});
