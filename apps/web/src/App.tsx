import { useEffect, useState } from 'react';
import { fetchScene } from './api';
import { SceneViewport } from './SceneViewport';
import { DialogueDock } from './DialogueDock';
import { useVoiceRound } from './useVoiceRound';
import { CompanionPopover } from './CompanionPopover';
import { ArchetypePreview } from './ArchetypePreview';
import type { Entity } from './types';

export default function App() {
  const isPreview = window.location.hash === '#/dev/archetypes';
  if (isPreview) return <ArchetypePreview />;
  const [scene, setScene] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusEntity, setFocusEntity] = useState<Entity | null>(null);
  const { micOn, status, turns, companion, start, stop, beginUtterance, interrupt, askCompanion } = useVoiceRound('sess-1', `ws://${location.hostname}:8000/ws/sessions/sess-1`);

  useEffect(() => {
    fetchScene('scene_bakery_001').then(setScene).catch((e) => setError(String(e)));
  }, []);

  if (error) return <div>加载失败：{error}</div>;
  if (!scene) return <div>加载中…</div>;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{ padding: '8px 16px', display: 'flex', gap: 16, alignItems: 'center' }}>
        <strong>{scene.setting.displayName}</strong>
        <span>{scene.setting.time}</span>
        <button onClick={micOn ? stop : start}>{micOn ? '停止' : '开始语音'}</button>
        <button onClick={beginUtterance} disabled={!micOn}>按住说话</button>
        <button onClick={interrupt}>打断</button>
      </header>
      <div style={{ flex: 1, padding: 16, position: 'relative' }}>
        <SceneViewport scene={scene} onEntityClick={(e) => setFocusEntity(e)} />
      </div>
      <DialogueDock turns={turns} status={status} />
      {focusEntity && <CompanionPopover entity={focusEntity} companion={companion} onAsk={askCompanion} onClose={() => setFocusEntity(null)} />}
    </div>
  );
}
