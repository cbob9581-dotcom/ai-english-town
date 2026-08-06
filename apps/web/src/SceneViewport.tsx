import type { CSSProperties } from 'react';
import { useRef, useState } from 'react';
import type { Entity } from './types';
import { ensureMinHit, mapLogicalToCss } from './coords';
import { renderEntity } from './registry';

interface Props {
  scene: { entities: Entity[]; setting: { displayName: string; time: string } };
  onEntityClick?: (entity: Entity) => void;
}

export function SceneViewport({ scene, onEntityClick }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 阶段 1 简化：固定假设逻辑视口 1000×600（与 0..1000 坐标一致），
  // ResizeObserver 真实容器适配留阶段 2。
  const [size] = useState({ w: 1000, h: 600 });
  const style: CSSProperties = {
    position: 'relative', overflow: 'hidden', borderRadius: 12,
    width: '100%', height: '100%',
    background: 'linear-gradient(#ffe8c8 0%, #ffd9a0 55%, #a9744b 56%, #8a5a34 100%)',
  };

  return (
    <div ref={boxRef} style={style} data-testid="scene-viewport">
      {/* BackgroundLayer 阶段 1 用原型 gradient（由 archetype.background.gradient 提供） */}
      {scene.entities.map((entity) => {
        const css = ensureMinHit(mapLogicalToCss(entity.layout.x, entity.layout.y, entity.layout.w, entity.layout.h, size.w, size.h));
        return (
          <div key={entity.id} style={{ position: 'absolute', ...css }} data-entity={entity.id} onClick={() => onEntityClick?.(entity)}>
            {renderEntity(entity)}
          </div>
        );
      })}
    </div>
  );
}
