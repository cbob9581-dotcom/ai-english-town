import type { Entity } from './types';

interface Props {
  entity: Entity;
  companion: { word: string; scaffold: string } | null;
  onAsk: (entityId: string) => void;
  onClose: () => void;
}

export function CompanionPopover({ entity, companion, onAsk, onClose }: Props) {
  const word = entity.semantics?.name ?? '';
  return (
    <div data-testid="companion-popover" style={{ position: 'fixed', right: 16, bottom: 120, background: '#fff', border: '1px solid #ccc', borderRadius: 12, padding: 12, width: 280 }}>
      <strong>伴学者</strong>
      <p>这个词：<b>{word}</b></p>
      {companion && companion.word === word && (
        <p data-testid="companion-scaffold" style={{ color: '#333' }}>{companion.scaffold}</p>
      )}
      <button onClick={() => onAsk(entity.id)}>读给我听</button>
      <button onClick={onClose}>关闭</button>
    </div>
  );
}
