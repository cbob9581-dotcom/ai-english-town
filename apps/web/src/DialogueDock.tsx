export interface Turn { role: 'user' | 'npc'; text: string; turnId?: string; candidateWordIds?: string[]; }

export function DialogueDock({ turns, status }: { turns: Turn[]; status: string }) {
  return (
    <section style={{ position: 'fixed', bottom: 0, left: 0, right: 0, padding: 12, background: 'rgba(0,0,0,.55)', color: '#fff' }}>
      <div data-testid="status">{status}</div>
      {turns.map((t, i) => (
        <div key={i}><b>{t.role === 'user' ? '你' : 'Rosa'}</b>：{t.text}</div>
      ))}
    </section>
  );
}
