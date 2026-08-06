import { describe, it, expect } from 'vitest';
import { createWavPlayer } from '../src/audio/playback';

describe('wav player (阶段 1 播放端)', () => {
  it('skips empty buffers: returns null without touching Web Audio', () => {
    const player = createWavPlayer();
    expect(player.play(new ArrayBuffer(0))).toBeNull();
    expect(player.play(new ArrayBuffer(0))).toBeNull();
  });

  it('no-ops when Web Audio is unavailable (jsdom has no AudioContext)', () => {
    const player = createWavPlayer();
    // jsdom 无 AudioContext：真实解码/出声不可用，play 必须安全返回 null（不抛错）
    expect(player.play(new ArrayBuffer(640))).toBeNull();
  });
});
