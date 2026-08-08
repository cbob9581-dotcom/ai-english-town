import type { CSSProperties, MouseEvent } from 'react';
import { useRef, useState } from 'react';
import type { Entity } from './types';
import type { SceneStatus } from './sceneStore';
import { ensureMinHit, mapLogicalToCss } from './coords';
import { renderEntity } from './registry';
import { renderIcon } from './icons';
import { GestureLayer } from './GestureLayer';

interface Props {
  // setting/background 声明可空：store 中两者都是 `T | null`（skeleton 可能缺省），渲染零使用，仅为承接 App 传值。
  scene: { entities: Entity[]; setting: { displayName: string; time: string } | null; background?: { style: string; gradient: string; decor: string[]; ambienceKey: string } | null; exits?: { id: string; targetArchetypeId?: string }[] };
  status: SceneStatus;
  onExitClick: (exitId: string) => void;
  onHint: (exitId: string) => void;
  onNpcClick: (npcId: string) => void;
  onEntityClick?: (entity: Entity) => void;
  lastGesture: { type: string; entityId?: string } | null;
}

const DECOR_POS: Record<string, { x: number; y: number }> = {
  fountain: { x: 60, y: 60 }, tree: { x: 850, y: 90 }, bench: { x: 820, y: 720 },
  window: { x: 60, y: 90 }, shelf: { x: 160, y: 500 }, 'hanging-sign': { x: 500, y: 80 },
  sign: { x: 500, y: 80 },   // station/cafe background 用 "sign"（与 hanging-sign 同位置）
};

export function SceneViewport({ scene, status, onExitClick, onHint, onNpcClick, onEntityClick, lastGesture }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [size] = useState({ w: 1000, h: 600 });
  const bg = scene.background?.gradient ?? 'linear-gradient(#aee3ff 0%, #cdeffd 45%, #86b871 46%, #5d9e50 100%)';
  const style: CSSProperties = { position: 'relative', overflow: 'hidden', borderRadius: 12, width: '100%', height: '100%', background: bg };

  const handleClick = (_e: MouseEvent, entity: Entity) => {
    if (entity.component === 'door') onExitClick(entity.semantics.exitId ?? '');
    else if (entity.component === 'npc') onNpcClick(entity.semantics.npcId ?? '');
    else onEntityClick?.(entity);
  };

  return (
    <div ref={boxRef} style={style} data-testid="scene-viewport">
      {scene.background?.decor.map((key) => {
        const pos = DECOR_POS[key];
        if (!pos) return null;
        const css = mapLogicalToCss(pos.x, pos.y, 90, 90, size.w, size.h);
        return <span key={key} aria-hidden style={{ position: 'absolute', fontSize: 40, opacity: 0.5, ...css }}>{renderIcon(`decor.${key}`)}</span>;
      })}
      {scene.entities.map((entity) => {
        const css = ensureMinHit(mapLogicalToCss(entity.layout.x, entity.layout.y, entity.layout.w, entity.layout.h, size.w, size.h));
        const highlighted = lastGesture?.type === 'point' && lastGesture.entityId === entity.id;
        return (
          <div
            key={entity.id}
            data-testid={`entity-${entity.id}`}
            data-entity={entity.id}
            style={{ position: 'absolute', ...css, outline: highlighted ? '3px solid #ffd23e' : undefined, outlineOffset: 2 }}
            onMouseEnter={() => entity.component === 'door' && onHint(entity.semantics.exitId ?? '')}
            onClick={(e) => handleClick(e, entity)}
          >
            {renderEntity(entity)}
          </div>
        );
      })}
      <GestureLayer gesture={lastGesture} entities={scene.entities} size={size} />
      {status === 'skeleton' && <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(100deg, transparent 30%, rgba(255,255,255,.25) 50%, transparent 70%)', animation: 'shimmer 1.2s infinite', pointerEvents: 'none' }} />}
      {status === 'degraded' && <div style={{ position: 'absolute', top: 8, right: 8, background: '#fbb', borderRadius: 6, padding: '0 8px' }}>简易场景</div>}
    </div>
  );
}
