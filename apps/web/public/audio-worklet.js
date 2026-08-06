// 48kHz 输入 → 16kHz mono → 每 20ms 一帧发回主线程（PCM16 字节）
class PcmCollector extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(0); }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    this.buf = concat(this.buf, input);
    const ratio = sampleRate / 16000;
    const out = new Float32Array(Math.floor(this.buf.length / ratio));
    for (let i = 0; i < out.length; i++) {
      const pos = i * ratio; const i0 = Math.floor(pos); const i1 = Math.min(i0 + 1, this.buf.length - 1);
      out[i] = this.buf[i0] * (1 - (pos - i0)) + this.buf[i1] * (pos - i0);
    }
    this.buf = this.buf.slice(this.buf.length % Math.max(1, Math.round(ratio)));
    // 发送成块
    for (let i = 0; i < out.length; i += 320) {
      const frame = out.subarray(i, Math.min(i + 320, out.length));
      const bytes = new ArrayBuffer(frame.length * 2);
      const view = new DataView(bytes);
      for (let j = 0; j < frame.length; j++) {
        const s = Math.max(-1, Math.min(1, frame[j]));
        view.setInt16(j * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      }
      this.port.postMessage(bytes, [bytes]);
    }
    return true;
  }
}

function concat(a, b) {
  const c = new Float32Array(a.length + b.length);
  c.set(a); c.set(b);
  return c;
}

registerProcessor('pcm-collector', PcmCollector);
