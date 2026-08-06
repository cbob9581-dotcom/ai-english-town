import { describe, it, expect } from 'vitest';
import { resampleTo16kMono } from '../src/audio/resample';

describe('resampleTo16kMono', () => {
  it('keeps a 1kHz sine at the correct dominant frequency after 48k→16k', () => {
    const rate = 48000; const n = 4800; // 0.1s
    const input = new Float32Array(n);
    for (let i = 0; i < n; i++) input[i] = Math.sin(2 * Math.PI * 1000 * (i / rate));
    const out = resampleTo16kMono(input, rate);
    expect(out.length).toBeGreaterThan(1500);
    expect(out.length).toBeLessThan(1700);
    // 过零率 ≈ 2kHz / 16kHz
    const zeros = countZeroCrossings(out);
    expect(Math.abs(zeros / (out.length / 16000) - 2000)).toBeLessThan(120);
  });
});

function countZeroCrossings(s: Float32Array): number {
  let c = 0;
  for (let i = 1; i < s.length; i++) if (s[i - 1] < 0 !== s[i] < 0) c++;
  return c;
}
