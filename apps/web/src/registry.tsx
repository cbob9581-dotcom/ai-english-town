import type { JSX } from 'react';
import type { Entity } from './types';
import { renderIcon } from './icons';

// 组件白名单：未知 component 一律渲染 null，绝不执行任意代码。
export function renderEntity(entity: Entity): JSX.Element | null {
  const { component } = entity;
  switch (component) {
    case 'prop':
    case 'npc':
    case 'companion':
    case 'door':
      return (
        <button
          type="button"
          aria-label={entity.semantics.name}
          style={{
            position: 'absolute', fontSize: 'min(7vmin, 44px)', lineHeight: 1,
            background: 'transparent', border: 'none', cursor: 'pointer',
            width: '100%', height: '100%',
          }}
        >
          <span role="img" aria-hidden>{renderIcon(entity.appearance.visualKey)}</span>
          <span style={{ display: 'block', fontSize: '12px', background: 'rgba(255,255,255,.85)', borderRadius: 6, padding: '0 4px' }}>
            {entity.semantics.name}
          </span>
        </button>
      );
    default:
      return null; // image/label/dialogue-zone/ambient-audio 阶段 1 不渲染为可点实体
  }
}
