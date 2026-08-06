import { useEffect, useState } from 'react';
import { fetchScene } from './api';
import { SceneViewport } from './SceneViewport';

export default function App() {
  const [scene, setScene] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

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
        <span>自由模式</span>
      </header>
      <div style={{ flex: 1, padding: 16 }}>
        <SceneViewport scene={scene} />
      </div>
    </div>
  );
}
