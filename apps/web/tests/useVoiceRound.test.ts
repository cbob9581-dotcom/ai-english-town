import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceRound } from '../src/useVoiceRound';
import { useSceneStore } from '../src/sceneStore';

/** 被 mock 的 playback 记录每次 play() 收到的 buffer，用于断言"播的是哪一段音频"。 */
const { playedBuffers } = vi.hoisted(() => ({ playedBuffers: [] as ArrayBuffer[] }));

vi.mock('../src/audio/playback', () => ({
  createWavPlayer: () => ({
    play: (buffer: ArrayBuffer) => {
      playedBuffers.push(buffer);
      return { stop: () => {} };
    },
  }),
}));

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
    static last: FakeMic | null = null;
    onChunk: ((c: ArrayBuffer) => void) | null = null;
    constructor() { FakeMic.last = this; }
    async start() {}
    stop() {}
  }
  return { Mic: FakeMic };
});

vi.mock('../src/audio/rms-gate', async (importOriginal) => {
  const { RmsGate } = await importOriginal<typeof import('../src/audio/rms-gate')>();
  return { RmsGate };
});

afterEach(() => {
  playedBuffers.length = 0;
  vi.restoreAllMocks();
});

/** RmsGate：连续 15 帧（holdMs=300 / frameMs=20）以上阈值 → 'speech'；连续 11 帧静音 → 'end'。 */
function loudFrame(amp = 0.5, samples = 640): ArrayBuffer {
  const buf = new ArrayBuffer(samples * 2);
  const v = new DataView(buf);
  for (let i = 0; i < samples; i++) v.setInt16(i * 2, amp * 32767, true);
  return buf;
}

function silentFrame(samples = 640): ArrayBuffer {
  return new ArrayBuffer(samples * 2);
}

/** 模拟用户说完一句话（mic 静音 → 'end' → listeningRef 复位，回合才能再开）。 */
function endUtterance(mic: { onChunk: ((c: ArrayBuffer) => void) | null }) {
  for (let i = 0; i < 11; i++) mic.onChunk!(silentFrame());
}

/** start() 里才 new VoiceSocket()/new Mic()——实例只能在 start 之后从 mock 取。 */
async function startHook() {
  const { VoiceSocket } = await import('../src/audio/ws-client');
  const { result } = renderHook(() => useVoiceRound('s', 'ws://x'));
  await act(async () => { await result.current.start(); });
  const sock = VoiceSocket.last!;
  const mic = (await import('../src/audio/mic')).Mic.last!;
  const emit = (type: string, payload: any) => {
    for (const h of sock.handlers.get(type) ?? []) h(payload);
  };
  // 门改造前提：先播种 scene.skeleton（genId 'g'），让既有语音 fixture 的 genId 全部命中
  // currentGenIdRef；否则 currentGenIdRef=null 会拒掉所有语音消息。纯语音用例不读 store，无害。
  act(() => emit('scene.skeleton', {
    type: 'scene.skeleton', sceneId: 's', generationId: 'g', archetypeId: 'plaza',
    revision: 1, status: 'skeleton',
    setting: { displayName: 'Town', time: 'day' },
    background: { style: 'gradient', gradient: 'linear-gradient(#000,#111)', decor: [], ambienceKey: 'a' },
    entities: [], characters: [], exits: [],
  }));
  return { result, sock, mic, emit };
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

  it('multi-round：round 2 渲染 + round 2 开始后迟到的 round-1 消息被丢弃', async () => {
    const { result, mic, emit } = await startHook();
    // round 1
    act(() => result.current.beginUtterance());
    act(() => {
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Hello!' });
      emit('npc.speech.commit', { type: 'npc.speech.commit', generationId: 'g', turnId: 't1', text: 'Hello!' });
    });
    // 用户说完 → mic 'end' → listeningRef 复位
    act(() => endUtterance(mic));
    // round 2：beginUtterance 必须 reset currentTurnId，round-2 的 t2 才能进门
    act(() => result.current.beginUtterance());
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't2', text: 'Bye.' }));
    // round 2 开始后迟到的 round-1 消息 → 丢弃
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Late junk.' }));
    expect(result.current.turns.map((t) => t.text)).toEqual(['Hello!', 'Bye.']);
  });

  it('barge-in 重叠：null 窗口内迟到的 round-1 delta 被丢弃，round-2 才 bootstrap', async () => {
    const { result, mic, emit } = await startHook();
    // round 1（说完才流式返回）
    act(() => result.current.beginUtterance());
    act(() => endUtterance(mic));
    act(() => {
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'A loaf.' });
      emit('npc.speech.commit', { type: 'npc.speech.commit', generationId: 'g', turnId: 't1', text: 'A loaf.' });
    });
    expect(result.current.turns.length).toBe(1);
    // 用户又开口（mic 'speech' barge-in）：current 复位为 null
    act(() => { for (let i = 0; i < 15; i++) mic.onChunk!(loudFrame()); });
    // 上一轮尾部消息在 null 窗口内迟到 → 丢弃（lastTurnIdRef='t1'），不 bootstrap
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Late junk.' }));
    expect(result.current.turns.length).toBe(1);
    expect(result.current.turns[0].text).toBe('A loaf.');
    // 真正的新 turnId 才 bootstrap 进 round 2
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't2', text: 'Bye.' }));
    expect(result.current.turns.map((t) => t.text)).toEqual(['A loaf.', 'Bye.']);
  });

  it('barge-in：本地清空音频队列，迟到的上一轮音频不播放', async () => {
    const { result, sock, mic, emit } = await startHook();
    act(() => result.current.beginUtterance());
    act(() => endUtterance(mic));
    const chunkA = new ArrayBuffer(4);
    const chunkB = new ArrayBuffer(6);
    // round-1 音频入队（chunkA 仍在队列——tts.audio.end 未到，服务器被 barge-in 取消）
    act(() => {
      emit('tts.audio.start', { type: 'tts.audio.start', generationId: 'g', turnId: 't1', chunkId: 's1', sampleRate: 16000 });
      emit('audio.binary', chunkA);
    });
    // 用户开口 barge-in：清空队列 + 复位 audioTurnId + 发 audio.start
    act(() => { for (let i = 0; i < 15; i++) mic.onChunk!(loudFrame()); });
    expect(sock.sent.filter((m: any) => m.type === 'audio.start').length).toBe(2); // 回合开始 + barge-in
    // round-2 音频：只能播到 chunkB（chunkA 已被清掉，FIFO 不会再先出队）
    act(() => {
      emit('tts.audio.start', { type: 'tts.audio.start', generationId: 'g', turnId: 't2', chunkId: 's1', sampleRate: 16000 });
      emit('audio.binary', chunkB);
      emit('tts.audio.end', { type: 'tts.audio.end', generationId: 'g', turnId: 't2', chunkId: 's1' });
    });
    expect(playedBuffers).toEqual([chunkB]);
  });

  it('companion.ask 只发 entityId；reply 写入 companion state', async () => {
    const { result, sock, emit } = await startHook();
    act(() => result.current.askCompanion('loaf-1'));
    expect(sock.sent).toEqual([{ type: 'companion.ask', entityId: 'loaf-1' }]);
    act(() => emit('companion.reply', { type: 'companion.reply', turnId: 'c1', word: 'loaf', scaffold: 'A loaf is bread.', degraded: false }));
    expect(result.current.companion).toEqual({ word: 'loaf', scaffold: 'A loaf is bread.' });
  });
});

describe('useVoiceRound scene messages', () => {
  // zustand 是模块级单例：跨用例 reset，避免上个用例的 store 状态残留污染 gate/发送 payload。
  beforeEach(() => {
    useSceneStore.getState().reset();
  });

  it('scene.skeleton populates store and gates speech by generation', async () => {
    const { result, emit } = await startHook();
    act(() => {
      emit('scene.skeleton', {
        type: 'scene.skeleton', sceneId: 's1', generationId: 'g1', archetypeId: 'plaza',
        revision: 1, status: 'skeleton',
        setting: { displayName: 'Town', time: 'day' },
        background: { style: 'gradient', gradient: 'linear-gradient(#000,#111)', decor: [], ambienceKey: 'a' },
        entities: [{ id: 'guide-1', component: 'npc', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'npc.greeter' }, semantics: { name: 'Tom', npcId: 'npc_tom' } }],
        characters: [{ slotId: 'guide', npcId: 'npc_tom' }],
        exits: [{ id: 'left', targetArchetypeId: 'bakery' }],
      });
    });
    expect(useSceneStore.getState().generationId).toBe('g1');
    expect(useSceneStore.getState().entities.length).toBe(1);
    // 跨场景（g2）的 delta 被拒
    act(() => result.current.beginUtterance());
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g2', turnId: 't1', text: 'junk' }));
    expect(result.current.turns.length).toBe(0);
  });

  it('scene.patch applies via gate and flips status to filled', async () => {
    const { result, emit } = await startHook();
    act(() => {
      emit('scene.skeleton', { type: 'scene.skeleton', sceneId: 's1', generationId: 'g1', archetypeId: 'bakery', revision: 1, status: 'skeleton', setting: { displayName: 'X', time: 'day' }, background: { style: 'gradient', gradient: 'g', decor: [], ambienceKey: 'a' }, entities: [], characters: [], exits: [] });
      emit('scene.patch', { type: 'scene.patch', sceneId: 's1', generationId: 'g1', baseRevision: 1, patchId: 'p1', ops: [{ op: 'replace', path: '/setting', value: { displayName: 'Y', time: 'morning' } }] });
    });
    expect(useSceneStore.getState().status).toBe('filled');
    expect(useSceneStore.getState().setting.displayName).toBe('Y');
    // baseRevision 不匹配的晚到 patch 被丢
    act(() => emit('scene.patch', { type: 'scene.patch', sceneId: 's1', generationId: 'g1', baseRevision: 9, patchId: 'p2', ops: [{ op: 'replace', path: '/setting', value: { displayName: 'Z', time: 'night' } }] }));
    expect(useSceneStore.getState().setting.displayName).toBe('Y');
  });

  it('scene.request / npc.focus send controls', async () => {
    const { result, sock } = await startHook();
    act(() => result.current.requestScene('left'));
    act(() => result.current.focusNpc('npc_rosa'));
    expect(sock.sent.some((m: any) => m.type === 'scene.request' && m.exitId === 'left')).toBe(true);
    expect(sock.sent.some((m: any) => m.type === 'npc.focus' && m.characterId === 'npc_rosa')).toBe(true);
  });
});
