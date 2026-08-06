import { frameControl, isControlFrame, type ControlMessage, type FramedControl } from './ws-framing';

type Handler = (payload: unknown) => void;

export class VoiceSocket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private sessionId: string;
  private seq = 0;

  constructor(sessionId: string) { this.sessionId = sessionId; }

  connect(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => resolve();
      ws.onerror = (e) => reject(e);
      ws.onmessage = (e) => {
        if (isControlFrame(e.data)) {
          const framed = JSON.parse(e.data) as FramedControl;
          this.handlers.get(framed.type)?.forEach((h) => h(framed));
        } else {
          this.handlers.get('audio.binary')?.forEach((h) => h(e.data));
        }
      };
      this.ws = ws;
    });
  }

  sendControl(msg: ControlMessage): void {
    this.ws?.send(JSON.stringify(frameControl(this.sessionId, this.seq++, msg)));
  }

  sendAudioChunk(chunk: ArrayBuffer): void {
    this.ws?.send(chunk);
  }

  on(type: string, handler: Handler): void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(handler);
  }

  close(): void { this.ws?.close(); }
}
