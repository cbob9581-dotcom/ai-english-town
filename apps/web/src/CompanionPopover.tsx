export function CompanionPopover({ word, onAsk, onClose }: { word: string; onAsk: (w: string) => void; onClose: () => void }) {
  return (
    <div data-testid="companion-popover" style={{ position: 'fixed', right: 16, bottom: 120, background: '#fff', border: '1px solid #ccc', borderRadius: 12, padding: 12, width: 260 }}>
      <strong>伴学者</strong>
      <p>这个词：<b>{word}</b></p>
      <button onClick={() => onAsk(word)}>这个怎么说</button>
      <button onClick={() => onAsk('怎么读')}>怎么读</button>
      <button onClick={onClose}>关闭</button>
    </div>
  );
}
