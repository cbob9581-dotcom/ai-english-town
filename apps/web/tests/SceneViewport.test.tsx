import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { SceneViewport } from '../src/SceneViewport';
import type { Entity } from '../src/types';

const door: Entity = { id: 'door-1', component: 'door', layout: { x: 60, y: 520, w: 90, h: 200, anchor: 'bottom' }, appearance: { visualKey: 'door.wooden' }, semantics: { name: 'door', exitId: 'left', targetArchetypeId: 'bakery' }, interactions: ['pick'] };
const npc: Entity = { id: 'npc-guide', component: 'npc', layout: { x: 400, y: 600, w: 90, h: 90, anchor: 'bottom' }, appearance: { visualKey: 'npc.greeter' }, semantics: { name: 'Tom', npcId: 'npc_tom' }, interactions: ['focus'] };

function baseScene() {
  return { entities: [door, npc], setting: { displayName: 'X', time: 'day' }, background: { style: 'gradient', gradient: 'linear-gradient(#000,#111)', decor: [], ambienceKey: 'a' }, exits: [{ id: 'left', targetArchetypeId: 'bakery' }] };
}

describe('SceneViewport', () => {
  it('door click fires onExitClick, npc click fires onNpcClick, prop fires onEntityClick', () => {
    const onExit = vi.fn(); const onNpc = vi.fn(); const onEntity = vi.fn();
    render(<SceneViewport scene={baseScene() as any} status="filled" onExitClick={onExit} onHint={() => {}} onNpcClick={onNpc} onEntityClick={onEntity} lastGesture={null} />);
    fireEvent.click(screen.getByTestId('entity-door-1'));
    expect(onExit).toHaveBeenCalledWith('left');
    fireEvent.click(screen.getByTestId('entity-npc-guide'));
    expect(onNpc).toHaveBeenCalledWith('npc_tom');
  });

  it('degraded badge shows when status degraded', () => {
    render(<SceneViewport scene={baseScene() as any} status="degraded" onExitClick={() => {}} onHint={() => {}} onNpcClick={() => {}} onEntityClick={() => {}} lastGesture={null} />);
    expect(screen.getByText('简易场景')).toBeInTheDocument();
  });
});
