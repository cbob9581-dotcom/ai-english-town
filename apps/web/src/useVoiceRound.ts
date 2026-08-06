import { useRef, useState } from 'react';
import { Mic } from './audio/mic';
import { RmsGate } from './audio/rms-gate';
import { VoiceSocket } from './audio/ws-client';
import { AudioQueue } from './audio/playback-queue';
import type { Turn } from './DialogueDock';

export function useVoiceRound(sessionId: string, wsUrl: string) {
  const [micOn, setMicOn] = useState(false);
  const [status, setStatus] = useState('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const socketRef = useRef<VoiceSocket | null>(null);
  const queueRef = useRef(new AudioQueue());
  const micRef = useRef<Mic | null>(null);
  const gateRef = useRef(new RmsGate());
  const uttRef = useRef(0);

  const start = async () => {
    const sock = new VoiceSocket(sessionId);
    await sock.connect(wsUrl);
    socketRef.current = sock;
    sock.on('npc.speech.commit', (m: any) => setTurns((t) => [...t, { role: 'npc', text: m.text }]));
    sock.on('tts.audio.start', () => setStatus('speaking'));
    sock.on('tts.audio.end', () => setStatus('idle'));
    sock.on('audio.binary', (chunk) => queueRef.current.enqueue('x', chunk as ArrayBuffer));
    const mic = new Mic();
    mic.onChunk = (chunk) => {
      sock.sendAudioChunk(chunk);          // 转发音频帧到服务器（brief 漏了这行）
      const tag = gateRef.current.feed(chunk);
      if (tag === 'end') { sock.sendControl({ type: 'audio.end', utteranceId: `u${uttRef.current}` }); setStatus('listening'); }
    };
    await mic.start();
    micRef.current = mic;
    setMicOn(true);
  };

  const beginUtterance = () => {
    uttRef.current += 1;
    socketRef.current?.sendControl({ type: 'audio.start', utteranceId: `u${uttRef.current}`, languageMode: 'en' });
  };

  const stop = () => { micRef.current?.stop(); socketRef.current?.close(); setMicOn(false); };

  const interrupt = () => {
    socketRef.current?.sendControl({ type: 'playback.interrupted' });
    queueRef.current.clear();
  };

  return { micOn, status, turns, start, stop, beginUtterance, interrupt };
}
