export class RmsGate {
  private threshold: number;
  private silenceFrames: number;
  private silence = 0;

  constructor(threshold = 0.008, silenceFrames = 11) {
    this.threshold = threshold;
    this.silenceFrames = silenceFrames;
  }

  feed(frame: ArrayBuffer): 'speech' | 'silence' | 'end' {
    const view = new DataView(frame);
    let sum = 0;
    for (let i = 0; i < view.byteLength; i += 2) {
      const s = view.getInt16(i, true) / 32768;
      sum += s * s;
    }
    const rms = Math.sqrt(sum / (view.byteLength / 2));
    if (rms >= this.threshold) { this.silence = 0; return 'speech'; }
    this.silence++;
    return this.silence >= this.silenceFrames ? 'end' : 'silence';
  }
}
