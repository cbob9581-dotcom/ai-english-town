import { useEffect, useState } from 'react';
import type { Entity } from './types';
import { ensureMinHit, mapLogicalToCss } from './coords';
import { renderEntity } from './registry';

interface ArcheInfo {
  archetypeId: string;
  displayName: string;
  skeleton: { setting: any; entities: Entity[]; exits: { id: string; targetArchetypeId?: string }[] };
  zones: Record<string, { x: [number, number]; y: [number, number]; anchor: string }>;
  propSlots: { slotId: string; zone: string; categories: string[] }[];
}

export function ArchetypePreview() {
  const [list, setList] = useState<ArcheInfo[]>([]);
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    fetch('/api/dev/archetypes').then((r) => r.json()).then(setList).catch(() => setList([]));
  }, []);
  if (list.length === 0) return <div>预览数据加载中…（需要 API 8000）</div>;
  const cur = list[idx % list.length];
  const size = { w: 1000, h: 600 };
  return (
    <div style={{ padding: 16 }}>
      <button onClick={() => setIdx((i) => (i + list.length - 1) % list.length)}>←</button>
      <strong>{cur.displayName} ({cur.archetypeId})</strong>
      <button onClick={() => setIdx((i) => (i + 1) % list.length)}>→</button>
      <div style={{ position: 'relative', width: 800, height: 480, overflow: 'hidden', borderRadius: 12, background: 'linear-gradient(#aee3ff 0%, #cdeffd 45%, #86b871 46%, #5d9e50 100%)' }}>
        {Object.entries(cur.zones).map(([name, z]) => {
          const css = ensureMinHit(mapLogicalToCss(z.x[0], z.y[0], z.x[1] - z.x[0], z.y[1] - z.y[0], size.w, size.h));
          return <div key={name} style={{ position: 'absolute', border: '1px dashed #f33', ...css }} />;
        })}
        {cur.skeleton.entities.map((e) => {
          const css = ensureMinHit(mapLogicalToCss(e.layout.x, e.layout.y, e.layout.w, e.layout.h, size.w, size.h));
          return (
            <div key={e.id} style={{ position: 'absolute', ...css }} data-entity={e.id}>
              {renderEntity(e)}
            </div>
          );
        })}
      </div>
      <p>实体数 {cur.skeleton.entities.length} / 40；出口 {cur.skeleton.exits.map((x) => `${x.id}→${x.targetArchetypeId}`).join(', ')}</p>
    </div>
  );
}
