import { useRef, useState } from 'react';
import { Mic } from './audio/mic';
import { RmsGate } from './audio/rms-gate';
import { VoiceSocket } from './audio/ws-client';
import { AudioQueue } from './audio/playback-queue';
import { createWavPlayer, type PlaybackHandle, type WavPlayer } from './audio/playback';
import type { Turn } from './DialogueDock';

export function useVoiceRound(sessionId: string, wsUrl: string) {
  const [micOn, setMicOn] = useState(false);
  const [status, setStatus] = useState('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const socketRef = useRef<VoiceSocket | null>(null);
  const queueRef = useRef(new AudioQueue());
  const micRef = useRef<Mic | null>(null);
  const gateRef = useRef(new RmsGate());
  const playerRef = useRef<WavPlayer | null>(null);
  const activePlaybackRef = useRef<PlaybackHandle | null>(null);
  const uttRef = useRef(0);

  const ensurePlayer = () => {
    if (!playerRef.current) playerRef.current = createWavPlayer();
    return playerRef.current;
  };

  const start = async () => {
    const sock = new VoiceSocket(sessionId);
    await sock.connect(wsUrl);
    socketRef.current = sock;
    sock.on('npc.speech.commit', (m: any) => setTurns((t) => [...t, { role: 'npc', text: m.text }]));
    sock.on('tts.audio.start', () => setStatus('speaking'));
    sock.on('tts.audio.end', () => {
      setStatus('idle');
      // 阶段 1：服务器在 start/end 之间发单块完整 WAV；end 时取出并播放
      const item = queueRef.current.next();
      if (item) activePlaybackRef.current = ensurePlayer().play(item.buffer);
    });
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

  const stop = () => {
    activePlaybackRef.current?.stop();
    activePlaybackRef.current = null;
    micRef.current?.stop();
    socketRef.current?.close();
    setMicOn(false);
  };

  const interrupt = () => {
    activePlaybackRef.current?.stop();
    activePlaybackRef.current = null;
    socketRef.current?.sendControl({ type: 'playback.interrupted' });
    queueRef.current.clear();
  };

  const askCompanion = (word: string) => {
    // 阶段 1 占位：后续接 askCompanion 触发 TTS 读单词
    alert(`(阶段1占位) 伴学者读：${word}`);
  };

  return { micOn, status, turns, start, stop, beginUtterance, interrupt, askCompanion };
}
