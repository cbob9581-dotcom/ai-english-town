import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RmsGate } from '../src/audio/rms-gate';

function loudFrame(amp = 0.5, samples = 320): ArrayBuffer {
  const buf = new ArrayBuffer(samples * 2);
  const v = new DataView(buf);
  for (let i = 0; i < samples; i++) v.setInt16(i * 2, amp * 32767, true);
  return buf;
}

function silentFrame(samples = 320): ArrayBuffer {
  return new ArrayBuffer(samples * 2);
}

describe('RmsGate ducking + barge-in hold', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('reports speech only after 300ms of above-threshold RMS', () => {
    const g = new RmsGate(0.008, 11, 300, 2, 20);  // frameMs 20 → 连续 15 帧
    for (let i = 0; i < 14; i++) expect(g.feed(loudFrame())).toBe('silence');
    expect(g.feed(loudFrame())).toBe('speech');
  });

  it('a single spike does not count as speech', () => {
    const g = new RmsGate();
    expect(g.feed(loudFrame())).toBe('silence');
    expect(g.feed(silentFrame())).toBe('silence');
  });

  it('ducking raises the threshold during playback and restores after 300ms', () => {
    const g = new RmsGate(0.008, 11, 300, 2, 20);
    g.setDucking(true);
    // 幅度 0.01：ducking 阈值 0.016 之下 → 10 帧全 silence（< silenceFrames=11）
    for (let i = 0; i < 10; i++) {
      expect(g.feed(loudFrame(0.01))).toBe('silence');
    }
    vi.advanceTimersByTime(400);  // 恢复窗口结束
    // 常态阈值 0.008：0.01 ≥ 0.008，holdMs=300（frameMs 20 → 连续 15 帧）→ 前 14 帧 silence，第 15 帧 speech
    for (let i = 0; i < 14; i++) {
      expect(g.feed(loudFrame(0.01))).toBe('silence');
    }
    expect(g.feed(loudFrame(0.01))).toBe('speech');
  });

  it('silence ends the utterance after silenceFrames', () => {
    const g = new RmsGate();
    for (let i = 0; i < 15; i++) g.feed(loudFrame());  // 先触发 speech
    for (let i = 0; i < 10; i++) g.feed(silentFrame());
    expect(g.feed(silentFrame())).toBe('end');
  });
});
