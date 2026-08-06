import { describe, it, expect } from 'vitest';
import { frameControl, isControlFrame, type ControlMessage } from '../src/audio/ws-framing';

describe('ws-framing', () => {
  it('frameControl tags a message with eventId/sessionId/timestamp/sequence', () => {
    const msg: ControlMessage = { type: 'audio.start', utteranceId: 'u1', languageMode: 'en' };
    const framed = frameControl('sess-1', 7, msg);
    expect(framed.sequence).toBe(7);
    expect(framed.eventId).toBeDefined();
    expect(framed.type).toBe('audio.start');
    expect(framed.utteranceId).toBe('u1');
  });

  it('detects control vs binary', () => {
    expect(isControlFrame('{"type":"audio.end"}')).toBe(true);
    // 二进制帧（ArrayBuffer 实例）不是控制帧
    expect(isControlFrame(new ArrayBuffer(4))).toBe(false);
  });
});
