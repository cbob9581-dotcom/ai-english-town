import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import { useSceneStore } from '../src/sceneStore';

/**
 * App 冒烟：守卫 Task 10 评审发现的 Critical——内联 store selector 未包 useShallow 时，
 * zustand v5 + React 19 的 useSyncExternalStore 用 Object.is 比较快照，每次渲染返回新对象 →
 * 恒判变更 → 无限重渲染崩溃（挂载即炸）。tsc/vitest 单元测试都测不到（没有任何测试渲染 App）。
 * 与 useVoiceRound.test.ts 相同的 mock 手法：useVoiceRound 依赖的 WS/mic/playback 全假实现。
 */
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
    constructor(public sessionId: string) {}
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
    constructor() {}
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
  cleanup();                       // 未开 globals：testing-library 不自动清理，显式清 DOM 防跨用例泄漏
  playedBuffers.length = 0;
  useSceneStore.getState().reset();
  vi.restoreAllMocks();
});

describe('App smoke', () => {
  it('mounts without infinite re-render and renders skeleton scene from store', () => {
    // store idle → 加载占位，挂载即验证不崩（无 useShallow 时这里就抛 Maximum update depth exceeded）
    const first = render(<App />);
    expect(screen.getByText(/加载中/)).toBeTruthy();
    first.unmount();

    // 注入 plaza skeleton → SceneViewport 渲染默认 NPC（Tom）
    useSceneStore.getState().applySkeleton({
      type: 'scene.skeleton', sceneId: 's1', generationId: 'g1', archetypeId: 'plaza',
      revision: 1, status: 'skeleton',
      setting: { displayName: 'Town', time: 'day' },
      background: { style: 'gradient', gradient: 'linear-gradient(#000,#111)', decor: [], ambienceKey: 'a' },
      entities: [{ id: 'guide-1', component: 'npc', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'npc.greeter' }, semantics: { name: 'Tom', npcId: 'npc_tom' } }],
      characters: [{ slotId: 'guide', npcId: 'npc_tom' }],
      exits: [{ id: 'left', targetArchetypeId: 'bakery' }],
    });
    render(<App />);
    expect(screen.getByTestId('scene-viewport')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Tom/i })).toBeTruthy();
  });

  it('idle 分支渲染「开始语音」按钮，点击即建立连接（防 idle 死锁回归）', async () => {
    // 守卫：status='idle' 时 App 早期 return 只给占位文本、按钮永远不渲染 → 用户永远无法 start()。
    // e2e scene.spec 直接 getByText('开始语音').click()，单测此前从未覆盖真实点击流。
    render(<App />);
    expect(screen.getByText(/加载中/)).toBeTruthy();
    const btn = screen.getByRole('button', { name: /开始语音/i });
    fireEvent.click(btn);          // start()：FakeVoiceSocket.connect + FakeMic.start 均为 no-op
    // mic 启动后 micOn=true → 按钮翻转为「停止」（status 仍 idle，仍在 idle 分支）
    await waitFor(() => expect(screen.getByRole('button', { name: /停止/i })).toBeTruthy());
  });
});
