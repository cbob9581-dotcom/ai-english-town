import { useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { SceneViewport } from './SceneViewport';
import { DialogueDock } from './DialogueDock';
import { useVoiceRound } from './useVoiceRound';
import { CompanionPopover } from './CompanionPopover';
import { ArchetypePreview } from './ArchetypePreview';
import ProgressView from './ProgressView';
import { useSceneStore } from './sceneStore';
import type { Entity } from './types';

export default function App() {
  // fetchScene 已移除：场景从 store 读，加载失败无来源 → 仅保留 error 渲染分支，setter 不再需要。
  const [error] = useState<string | null>(null);
  const [focusEntity, setFocusEntity] = useState<Entity | null>(null);
  const { micOn, micError, status, turns, companion, lastGesture, discovered, toggleDiscover, start, stop, beginUtterance, interrupt, askCompanion, entityClick, requestScene, hintScene, focusNpc } = useVoiceRound('sess-1', `ws://${location.hostname}:8000/ws/sessions/sess-1`);
  // 内联对象 selector 必须包 useShallow：zustand v5 + React 19 的 useSyncExternalStore 用
  // Object.is 比较快照，裸 selector 每次返回新对象 → 恒判变更 → 无限重渲染崩溃（挂载即炸）。
  const scene = useSceneStore(useShallow((s) => ({
    status: s.status, sceneId: s.sceneId, generationId: s.generationId,
    archetypeId: s.archetypeId, setting: s.setting, background: s.background,
    entities: s.entities, exits: s.exits,
  })));
  const isPreview = window.location.hash === '#/dev/archetypes';
  if (isPreview) return <ArchetypePreview />;
  const isProgress = window.location.hash === '#/progress';
  if (isProgress) return <ProgressView />;
  if (error) return <div>加载失败：{error}</div>;
  if (scene.status === 'idle') return (
    <div style={{ padding: 16 }}>
      <p>加载中…点「开始语音」连接语音、进入场景</p>
      <button onClick={micOn ? stop : start}>{micOn ? '停止' : '开始语音'}</button>
      {micError && <p style={{ color: '#b00' }}>启动失败：{micError}</p>}
    </div>
  );

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{ padding: '8px 16px', display: 'flex', gap: 16, alignItems: 'center' }}>
        <strong>{scene.setting?.displayName}</strong>
        <span>{scene.setting?.time}</span>
        {scene.status === 'degraded' && <span style={{ background: '#fbb', borderRadius: 6, padding: '0 6px' }}>简易场景</span>}
        <span>本场景 {discovered.size}/{scene.entities.length}</span>
        <button onClick={micOn ? stop : start}>{micOn ? '停止' : '开始语音'}</button>
        {micError && <span style={{ color: '#b00' }}>启动失败：{micError}</span>}
        <button onClick={beginUtterance} disabled={!micOn}>按住说话</button>
        <button onClick={interrupt}>打断</button>
      </header>
      <div style={{ flex: 1, padding: 16, position: 'relative' }}>
        <SceneViewport
          scene={scene}
          status={scene.status}
          onExitClick={requestScene}
          onHint={hintScene}
          onNpcClick={focusNpc}
          onEntityClick={(e) => { toggleDiscover(e.id); setFocusEntity(e); entityClick(e.id); }}
          lastGesture={lastGesture}
        />
      </div>
      <DialogueDock turns={turns} status={status} />
      {focusEntity && <CompanionPopover entity={focusEntity} companion={companion} onAsk={askCompanion} onClose={() => setFocusEntity(null)} />}
    </div>
  );
}
