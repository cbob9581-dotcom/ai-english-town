import { describe, it, expect } from 'vitest';
import { AudioQueue } from '../src/audio/playback-queue';

describe('AudioQueue', () => {
  it('plays chunks in order', () => {
    const q = new AudioQueue();
    q.enqueue('a', new ArrayBuffer(4)); q.enqueue('b', new ArrayBuffer(4));
    expect(q.next()?.id).toBe('a');
    expect(q.next()?.id).toBe('b');
  });

  it('clear drops pending chunks and reports count', () => {
    const q = new AudioQueue();
    q.enqueue('a', new ArrayBuffer(4)); q.enqueue('b', new ArrayBuffer(4));
    expect(q.clear()).toBe(2);
    expect(q.next()).toBeNull();
  });
});
