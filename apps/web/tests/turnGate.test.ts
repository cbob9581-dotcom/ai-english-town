import { describe, expect, it } from 'vitest';
import { acceptSceneMessage, acceptTurnMessage, isAcceptedTurn } from '../src/audio/turnGate';

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

describe('acceptTurnMessage', () => {
  it('rejects when generation differs (cross-scene late)', () => {
    expect(acceptTurnMessage('g2', 't2', null, 'g1', 't1')).toBe(false);
  });
  it('rejects same-scene stale turn', () => {
    expect(acceptTurnMessage('g1', 't2', null, 'g1', 't1')).toBe(false);
  });
  it('bootstraps new turn when currentTurnId is null', () => {
    expect(acceptTurnMessage('g1', null, null, 'g1', 't9')).toBe(true);
  });
  it('accepts companion turn', () => {
    expect(acceptTurnMessage('g1', 't1', 'c9', 'g1', 'c9')).toBe(true);
  });
});

describe('acceptSceneMessage', () => {
  it('rejects cross-scene patch', () => {
    expect(acceptSceneMessage('g2', 's2', 2, 'g1', 's1', 1)).toBe(false);
  });
  it('rejects mismatched baseRevision', () => {
    expect(acceptSceneMessage('g1', 's1', 3, 'g1', 's1', 1)).toBe(false);
  });
  it('accepts current patch', () => {
    expect(acceptSceneMessage('g1', 's1', 1, 'g1', 's1', 1)).toBe(true);
  });
});
