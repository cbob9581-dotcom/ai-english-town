import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceRound } from '../src/useVoiceRound';

vi.mock('../src/audio/ws-client', () => {
  class FakeVoiceSocket {
    handlers = new Map<string, Set<(m: any) => void>>();
    sent: any[] = [];
    static last: FakeVoiceSocket | null = null;
    constructor(public sessionId: string) { FakeVoiceSocket.last = this; }
    async connect(_url: string) {}
    sendControl(msg: any) { this.sent.push(msg); }
    sendAudioChunk() {}
    on(type: string, h: any) {
      if (!this.handlers.has(type)) this.handlers.set(type, new Set());
      this.handlers.get(type)!.add(h);
    }
    close() {}
  }
  return { VoiceSocket: FakeVoiceSocket };
});

vi.mock('../src/audio/mic', () => {
  class FakeMic {
    onChunk: ((c: ArrayBuffer) => void) | null = null;
    async start() {}
    stop() {}
  }
  return { Mic: FakeMic };
});

vi.mock('../src/audio/rms-gate', async (importOriginal) => {
  const { RmsGate } = await importOriginal<typeof import('../src/audio/rms-gate')>();
  return { RmsGate };
});

afterEach(() => { vi.restoreAllMocks(); });

/** start() 里才 new VoiceSocket()——socket 实例只能在 start 之后从 mock 取。 */
async function startHook() {
  const { VoiceSocket } = await import('../src/audio/ws-client');
  const { result } = renderHook(() => useVoiceRound('s', 'ws://x'));
  await act(async () => { await result.current.start(); });
  const sock = VoiceSocket.last!;
  const emit = (type: string, payload: any) => {
    for (const h of sock.handlers.get(type) ?? []) h(payload);
  };
  return { result, sock, emit };
}

describe('useVoiceRound protocol', () => {
  it('delta 累积 → commit 覆盖 + candidateWordIds', async () => {
    const { result, emit } = await startHook();
    act(() => result.current.beginUtterance());
    act(() => {
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Hello!' });
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Welcome.' });
      emit('npc.turn.metadata', { type: 'npc.turn.metadata', generationId: 'g', turnId: 't1', candidateWordIds: ['word_loaf_n_1'] });
      emit('npc.speech.commit', { type: 'npc.speech.commit', generationId: 'g', turnId: 't1', text: 'Hello! Welcome.' });
    });
    const last = result.current.turns[result.current.turns.length - 1];
    expect(last).toEqual({ role: 'npc', turnId: 't1', text: 'Hello! Welcome.', candidateWordIds: ['word_loaf_n_1'] });
  });

  it('stale turnId delta 被丢弃（不入字幕）', async () => {
    const { result, emit } = await startHook();
    act(() => result.current.beginUtterance());
    act(() => {
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'A loaf.' });
      emit('npc.speech.commit', { type: 'npc.speech.commit', generationId: 'g', turnId: 't1', text: 'A loaf.' });
    });
    const before = result.current.turns.length;
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't2', text: 'Late junk.' }));
    expect(result.current.turns.length).toBe(before);
    expect(result.current.turns[result.current.turns.length - 1].text).toBe('A loaf.');
  });

  it('companion.ask 只发 entityId；reply 写入 companion state', async () => {
    const { result, sock, emit } = await startHook();
    act(() => result.current.askCompanion('loaf-1'));
    expect(sock.sent.some((m: any) => m.type === 'companion.ask' && m.entityId === 'loaf-1')).toBe(true);
    act(() => emit('companion.reply', { type: 'companion.reply', turnId: 'c1', word: 'loaf', scaffold: 'A loaf is bread.', degraded: false }));
    expect(result.current.companion).toEqual({ word: 'loaf', scaffold: 'A loaf is bread.' });
  });
});
