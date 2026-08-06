export interface ControlMessage {
  type: string;
  utteranceId?: string;
  languageMode?: string;
  [k: string]: unknown;
}

export interface FramedControl extends ControlMessage {
  eventId: string;
  sessionId: string;
  timestamp: number;
  sequence: number;
}

let _seq = 0;

export function frameControl(sessionId: string, sequence: number, msg: ControlMessage): FramedControl {
  return {
    ...msg,
    eventId: `ev_${Date.now()}_${_seq++}`,
    sessionId,
    timestamp: Date.now(),
    sequence,
  };
}

export function isControlFrame(data: unknown): boolean {
  return typeof data === 'string';
}
