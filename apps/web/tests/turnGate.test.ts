import { describe, expect, it } from 'vitest';
import { isAcceptedTurn } from '../src/audio/turnGate';

describe('turnGate', () => {
  it('rejects null turnId', () => {
    expect(isAcceptedTurn('t1', null, null)).toBe(false);
    expect(isAcceptedTurn(null, null, undefined)).toBe(false);
  });

  it('accepts the current dialogue turnId', () => {
    expect(isAcceptedTurn('t1', null, 't1')).toBe(true);
  });

  it('accepts the pending companion turnId', () => {
    expect(isAcceptedTurn('t1', 'c9', 'c9')).toBe(true);
  });

  it('rejects a stale turnId (old round late arrival)', () => {
    expect(isAcceptedTurn('t2', null, 't1')).toBe(false);
  });
});
