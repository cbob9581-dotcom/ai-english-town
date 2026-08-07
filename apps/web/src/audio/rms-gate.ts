export class RmsGate {
  private threshold: number;
  private silenceFrames: number;
  private holdMs: number;
  private duckFactor: number;
  private frameMs: number;
  private silence = 0;
  private speechMs = 0;
  private duckUntil = 0;

  constructor(threshold = 0.008, silenceFrames = 11, holdMs = 300, duckFactor = 2, frameMs = 20) {
    this.threshold = threshold;
    this.silenceFrames = silenceFrames;
    this.holdMs = holdMs;
    this.duckFactor = duckFactor;
    this.frameMs = frameMs;
  }

  /** 播放开始/结束都启动 300ms 恢复窗口（播放结束后的回声尾巴也被压住）。 */
  setDucking(_on: boolean): void {
    this.duckUntil = performance.now() + 300;
  }

  private effectiveThreshold(): number {
    return performance.now() < this.duckUntil ? this.threshold * this.duckFactor : this.threshold;
  }

  feed(frame: ArrayBuffer): 'speech' | 'silence' | 'end' {
    const view = new DataView(frame);
    let sum = 0;
    for (let i = 0; i < view.byteLength; i += 2) {
      const s = view.getInt16(i, true) / 32768;
      sum += s * s;
    }
    const rms = Math.sqrt(sum / (view.byteLength / 2));
    if (rms >= this.effectiveThreshold()) {
      this.speechMs += this.frameMs;
      this.silence = 0;
      return this.speechMs >= this.holdMs ? 'speech' : 'silence';
    }
    this.speechMs = 0;
    this.silence++;
    return this.silence >= this.silenceFrames ? 'end' : 'silence';
  }
}
