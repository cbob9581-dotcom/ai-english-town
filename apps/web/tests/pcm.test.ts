import { describe, it, expect } from 'vitest';
import { encodePcm16, createAudioFrameChunks } from '../src/audio/pcm';

describe('pcm', () => {
  it('encodes [-1, 0, 1] as little-endian 16-bit', () => {
    const buf = encodePcm16(new Float32Array([-1, 0, 1]));
    const v = new DataView(buf);
    expect(v.getInt16(0, true)).toBe(-32768);
    expect(v.getInt16(2, true)).toBe(0);
    expect(v.getInt16(4, true)).toBe(32767);
  });

  it('splits into 20ms frames of 320 samples', () => {
    const frames = createAudioFrameChunks(new Float32Array(1000), 320);
    expect(frames.map((f) => f.length)).toEqual([320, 320, 320, 40]);
  });
});
