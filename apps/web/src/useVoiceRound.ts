import { useRef, useState } from 'react';
import { Mic } from './audio/mic';
import { RmsGate } from './audio/rms-gate';
import { VoiceSocket } from './audio/ws-client';
import { AudioQueue } from './audio/playback-queue';
import { createWavPlayer, type PlaybackHandle, type WavPlayer } from './audio/playback';
import { acceptTurnMessage, acceptSceneMessage } from './audio/turnGate';
import { useSceneStore } from './sceneStore';
import type { Turn } from './DialogueDock';

interface CompanionState { word: string; scaffold: string; }

export function useVoiceRound(sessionId: string, wsUrl: string) {
  const [micOn, setMicOn] = useState(false);
  const [status, setStatus] = useState('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [companion, setCompanion] = useState<CompanionState | null>(null);
  const [lastGesture, setLastGesture] = useState<{ type: string; entityId?: string } | null>(null);
  const [discovered, setDiscovered] = useState<Set<string>>(new Set());
  const toggleDiscover = (id: string) => {
    setDiscovered((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const socketRef = useRef<VoiceSocket | null>(null);
  const queueRef = useRef(new AudioQueue());
  const micRef = useRef<Mic | null>(null);
  const gateRef = useRef(new RmsGate());
  const playerRef = useRef<WavPlayer | null>(null);
  const activePlaybackRef = useRef<PlaybackHandle | null>(null);
  const uttRef = useRef(0);
  const listeningRef = useRef(false);
  const currentTurnIdRef = useRef<string | null>(null);
  const companionTurnIdRef = useRef<string | null>(null);
  const audioTurnIdRef = useRef<string | null>(null);
  const lastTurnIdRef = useRef<string | null>(null);
  const currentGenIdRef = useRef<string | null>(null);
  const hintTimerRef = useRef<number | null>(null);

  const ensurePlayer = () => {
    if (!playerRef.current) playerRef.current = createWavPlayer();
    return playerRef.current;
  };

  const stopPlayback = () => {
    activePlaybackRef.current?.stop();
    activePlaybackRef.current = null;
  };

  const nextUtterance = () => `u${++uttRef.current}`;

  /** delta 累积：同一 turnId 的句子追加到字幕（空格拼接）。 */
  const appendDelta = (turnId: string, sentence: string) => {
    setTurns((t) => {
      const copy = [...t];
      const last = copy[copy.length - 1];
      if (last && last.role === 'npc' && last.turnId === turnId) {
        copy[copy.length - 1] = { ...last, text: last.text ? `${last.text} ${sentence}` : sentence };
      } else {
        copy.push({ role: 'npc', turnId, text: sentence });
      }
      return copy;
    });
  };

  /** commit 是权威全文：覆盖已累积的 delta 字幕。 */
  const applyCommit = (turnId: string, text: string) => {
    setTurns((t) => {
      const copy = [...t];
      const last = copy[copy.length - 1];
      if (last && last.role === 'npc' && last.turnId === turnId) {
        copy[copy.length - 1] = { ...last, text };
      } else {
        copy.push({ role: 'npc', turnId, text });
      }
      return copy;
    });
  };

  const applyMetadata = (turnId: string, candidateWordIds: string[]) => {
    setTurns((t) => {
      const copy = [...t];
      const last = copy[copy.length - 1];
      if (last && last.role === 'npc' && last.turnId === turnId) {
        copy[copy.length - 1] = { ...last, candidateWordIds };
      }
      return copy;
    });
  };

  const start = async () => {
    const sock = new VoiceSocket(sessionId);
    await sock.connect(wsUrl);
    socketRef.current = sock;

    sock.on('scene.skeleton', (m: any) => {
      currentGenIdRef.current = m.generationId;
      currentTurnIdRef.current = null;      // 新场景：对话门 reset
      lastTurnIdRef.current = null;
      companionTurnIdRef.current = null;
      audioTurnIdRef.current = null;
      queueRef.current.clear();
      setDiscovered(new Set());             // 新场景：本场景点过的实体 reset
      useSceneStore.getState().applySkeleton(m);
    });
    sock.on('scene.patch', (m: any) => {
      const s = useSceneStore.getState();
      if (!acceptSceneMessage(currentGenIdRef.current, s.sceneId, s.revision, m.generationId, m.sceneId, m.baseRevision)) return;
      useSceneStore.getState().applyPatch(m);
    });
    sock.on('scene.degraded', (m: any) => {
      if (currentGenIdRef.current !== m.generationId) return;
      useSceneStore.getState().applyDegraded(m);
    });
    sock.on('scene.focus', (m: any) => {
      if (currentGenIdRef.current !== m.generationId) return;
      useSceneStore.getState().applyFocus(m);
    });
    sock.on('npc.speech.delta', (m: any) => {
      if (!acceptTurnMessage(currentGenIdRef.current, currentTurnIdRef.current, companionTurnIdRef.current, m.generationId, m.turnId)) return;
      if (currentTurnIdRef.current === null) {
        // null 窗口（新一轮刚开始）：丢弃上一轮尾部迟到的消息，仅真正的新 turnId 可 bootstrap
        if (m.turnId === lastTurnIdRef.current) return;
        currentTurnIdRef.current = m.turnId;
      }
      appendDelta(m.turnId, (m.text ?? '').trim());
    });
    sock.on('npc.speech.commit', (m: any) => {
      if (!acceptTurnMessage(currentGenIdRef.current, currentTurnIdRef.current, companionTurnIdRef.current, m.generationId, m.turnId)) return;
      if (currentTurnIdRef.current === null) {
        if (m.turnId === lastTurnIdRef.current) return;
        currentTurnIdRef.current = m.turnId;
      }
      lastTurnIdRef.current = m.turnId;  // 上一轮完整结束：记录其 turnId，供下一次 null 窗口拒收尾部
      applyCommit(m.turnId, m.text);
    });
    sock.on('npc.turn.metadata', (m: any) => {
      if (!acceptTurnMessage(currentGenIdRef.current, currentTurnIdRef.current, companionTurnIdRef.current, m.generationId, m.turnId)) return;
      if (currentTurnIdRef.current === null && m.turnId === lastTurnIdRef.current) return;
      applyMetadata(m.turnId, m.candidateWordIds ?? []);
      setLastGesture(m.gesture ?? null);
    });
    sock.on('companion.reply', (m: any) => {
      if (m.error) return;
      companionTurnIdRef.current = m.turnId;
      setCompanion({ word: m.word, scaffold: m.scaffold });
    });
    sock.on('tts.audio.start', (m: any) => {
      if (!acceptTurnMessage(currentGenIdRef.current, currentTurnIdRef.current, companionTurnIdRef.current, m.generationId, m.turnId)) {
        queueRef.current.clear();
        audioTurnIdRef.current = null;
        return;
      }
      if (currentTurnIdRef.current === null && m.turnId === lastTurnIdRef.current) {
        queueRef.current.clear();
        audioTurnIdRef.current = null;
        return;
      }
      audioTurnIdRef.current = m.turnId;
      gateRef.current.setDucking(true);
      setStatus('speaking');
    });
    sock.on('tts.audio.end', (m: any) => {
      if (currentGenIdRef.current !== m.generationId || audioTurnIdRef.current !== m.turnId) return;
      audioTurnIdRef.current = null;
      gateRef.current.setDucking(false);
      stopPlayback();
      const item = queueRef.current.next();
      if (item) activePlaybackRef.current = ensurePlayer().play(item.buffer);
      setStatus('idle');
    });
    sock.on('audio.binary', (chunk) => {
      if (audioTurnIdRef.current !== null) queueRef.current.enqueue(audioTurnIdRef.current, chunk as ArrayBuffer);
    });

    const mic = new Mic();
    mic.onChunk = (chunk) => {
      sock.sendAudioChunk(chunk);
      const tag = gateRef.current.feed(chunk);
      if (tag === 'speech' && !listeningRef.current) {
        // 语音触发开始；若正在播放 → 本地立即停播（barge-in 本地清理），audio.start 即打断信号
        stopPlayback();
        currentTurnIdRef.current = null;  // RULING 1：新一轮从空 current 开始
        queueRef.current.clear();         // 清掉上一轮仍在队列里的音频 chunk
        audioTurnIdRef.current = null;    // 停止接收迟到的 audio.binary 入队
        listeningRef.current = true;
        sock.sendControl({ type: 'audio.start', utteranceId: nextUtterance(), languageMode: 'en' });
        setStatus('listening');
      } else if (tag === 'end' && listeningRef.current) {
        sock.sendControl({ type: 'audio.end', utteranceId: `u${uttRef.current}` });
        listeningRef.current = false;
        setStatus('idle');
      }
    };
    await mic.start();
    micRef.current = mic;
    setMicOn(true);
  };

  const beginUtterance = () => {
    if (listeningRef.current) return;
    currentTurnIdRef.current = null;  // RULING 1：新一轮从空 current 开始
    listeningRef.current = true;
    socketRef.current?.sendControl({ type: 'audio.start', utteranceId: nextUtterance(), languageMode: 'en' });
  };

  const stop = () => {
    if (hintTimerRef.current !== null) {
      window.clearTimeout(hintTimerRef.current);
      hintTimerRef.current = null;
    }
    stopPlayback();
    micRef.current?.stop();
    socketRef.current?.close();
    setMicOn(false);
  };

  const interrupt = () => {
    stopPlayback();
    listeningRef.current = false;
    socketRef.current?.sendControl({ type: 'playback.interrupted' });
    queueRef.current.clear();
  };

  const askCompanion = (entityId: string) => {
    socketRef.current?.sendControl({ type: 'companion.ask', entityId });
  };

  const requestScene = (exitId: string) => {
    socketRef.current?.sendControl({ type: 'scene.request', exitId });
  };
  const hintScene = (exitId: string) => {
    if (hintTimerRef.current !== null) window.clearTimeout(hintTimerRef.current);
    hintTimerRef.current = window.setTimeout(() => {
      socketRef.current?.sendControl({ type: 'scene.hint', exitId });
    }, 300);
  };
  const focusNpc = (npcId: string) => {
    socketRef.current?.sendControl({ type: 'npc.focus', sceneId: useSceneStore.getState().sceneId, generationId: useSceneStore.getState().generationId, characterId: npcId });
  };

  return { micOn, status, turns, companion, lastGesture, discovered, toggleDiscover, start, stop, beginUtterance, interrupt, askCompanion, requestScene, hintScene, focusNpc };
}
