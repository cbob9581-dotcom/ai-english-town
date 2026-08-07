export class Mic {
  private ctx: AudioContext | null = null;
  private worklet: AudioWorkletNode | null = null;
  onChunk: ((chunk: ArrayBuffer) => void) | null = null;

  async start(): Promise<void> {
    this.ctx = new AudioContext({ sampleRate: 48000 });
    await this.ctx.audioWorklet.addModule('/audio-worklet.js');
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const source = this.ctx.createMediaStreamSource(stream);
    this.worklet = new AudioWorkletNode(this.ctx, 'pcm-collector');
    this.worklet.port.onmessage = (e) => this.onChunk?.(e.data as ArrayBuffer);
    source.connect(this.worklet);
  }

  stop(): void {
    this.worklet?.disconnect(); this.worklet = null;
    this.ctx?.close(); this.ctx = null;
  }
}
