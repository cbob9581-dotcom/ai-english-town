import { useState } from 'react';
import { SceneViewport } from './SceneViewport';
import { DialogueDock } from './DialogueDock';
import { useVoiceRound } from './useVoiceRound';
import { CompanionPopover } from './CompanionPopover';
import { ArchetypePreview } from './ArchetypePreview';
import { useSceneStore } from './sceneStore';
import type { Entity } from './types';

export default function App() {
  // fetchScene 已移除：场景从 store 读，加载失败无来源 → 仅保留 error 渲染分支，setter 不再需要。
  const [error] = useState<string | null>(null);
  const [focusEntity, setFocusEntity] = useState<Entity | null>(null);
  const { micOn, status, turns, companion, start, stop, beginUtterance, interrupt, askCompanion, requestScene, hintScene, focusNpc } = useVoiceRound('sess-1', `ws://${location.hostname}:8000/ws/sessions/sess-1`);
  const scene = useSceneStore((s) => ({
    status: s.status, sceneId: s.sceneId, generationId: s.generationId,
    archetypeId: s.archetypeId, setting: s.setting, background: s.background,
    entities: s.entities, exits: s.exits,
  }));
  const isPreview = window.location.hash === '#/dev/archetypes';
  if (isPreview) return <ArchetypePreview />;
  if (error) return <div>加载失败：{error}</div>;
  if (scene.status === 'idle') return <div>加载中…（需启动 API 8000 + 开始语音连接）</div>;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{ padding: '8px 16px', display: 'flex', gap: 16, alignItems: 'center' }}>
        <strong>{scene.setting?.displayName}</strong>
        <span>{scene.setting?.time}</span>
        {scene.status === 'degraded' && <span style={{ background: '#fbb', borderRadius: 6, padding: '0 6px' }}>简易场景</span>}
        <button onClick={micOn ? stop : start}>{micOn ? '停止' : '开始语音'}</button>
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
          onEntityClick={(e) => setFocusEntity(e)}
        />
      </div>
      <DialogueDock turns={turns} status={status} />
      {focusEntity && <CompanionPopover entity={focusEntity} companion={companion} onAsk={askCompanion} onClose={() => setFocusEntity(null)} />}
    </div>
  );
}
