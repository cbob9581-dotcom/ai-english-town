import type { Entity } from './types';
import { mapLogicalToCss } from './coords';

const EMOJI: Record<string, string> = { wave: '👋', nod: '✅', shake: '❌' };

export function GestureLayer({ gesture, entities, size }: { gesture: { type: string; entityId?: string } | null; entities: Entity[]; size: { w: number; h: number } }) {
  if (!gesture) return null;
  if (gesture.type === 'point') {
    const target = entities.find((e) => e.id === gesture.entityId);
    if (!target) return null;
    const css = mapLogicalToCss(target.layout.x, target.layout.y, target.layout.w, target.layout.h, size.w, size.h);
    return <span data-testid="gesture-point" style={{ position: 'absolute', left: css.left + css.width - 20, top: css.top - 30, fontSize: 32, pointerEvents: 'none' }}>👉</span>;
  }
  const emoji = EMOJI[gesture.type];
  if (!emoji) return null;
  return <span data-testid={`gesture-${gesture.type}`} style={{ position: 'absolute', left: 70, top: 40, fontSize: 40, animation: 'floaty 1s ease-in-out infinite', pointerEvents: 'none' }}>{emoji}</span>;
}
