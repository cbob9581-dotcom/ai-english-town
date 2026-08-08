import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';

interface WordSummary {
  wordId: string;
  lemma: string;
  pos: string;
  ipa: string | null;
  cefr: string | null;
  sceneTags: string[];
  source: string;
  carrier: string | null;
  scores: { productive: number; receptive: number; asrConfidence: number };
  fsrs: { state: string; due: string | null; reps: number; lapses: number };
  evidenceCount: number;
  lastEvidenceAt: string | null;
}

interface Summary {
  strategy: { evidencePolicyVersion: string; fsrsAlgorithmVersion: string };
  totals: { quest: number; free: number; dueToday: number };
  page: number;
  pageSize: number;
  totalWords: number;
  words: WordSummary[];
}

interface EvidenceItem {
  evidenceId: string;
  source: string;
  axis: string;
  result: string;
  confidence: number;
  created_at: string;
}

// 后端 strategy.fsrsAlgorithmVersion 形如 'fsrs-5'，角标展示为 'v5'（只取版本号）。
function fsrsVersionLabel(v: string): string {
  const m = /(\d+(?:\.\d+)?)/.exec(v);
  return m ? `v${m[1]}` : v;
}

const cardStyle: CSSProperties = {
  flex: 1,
  padding: '12px 16px',
  borderRadius: 10,
  border: '1px solid var(--border)',
  background: 'var(--accent-bg)',
  color: 'var(--text-h)',
};

const chipStyle: CSSProperties = {
  padding: '2px 8px',
  borderRadius: 999,
  border: '1px solid var(--border)',
  color: 'var(--text)',
  fontSize: 13,
};

const rowStyle: CSSProperties = {
  display: 'flex',
  gap: 12,
  alignItems: 'center',
  padding: '10px 16px',
  borderBottom: '1px solid var(--border)',
  cursor: 'pointer',
};

export default function ProgressView() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<Record<string, EvidenceItem[]>>({});
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async (p: number, ps: number) => {
    const res = await fetch(`/api/progress/summary?page=${p}&page_size=${ps}`);
    const data = await res.json();
    setSummary(data);
  }, []);

  useEffect(() => {
    loadSummary(page, pageSize).catch(() => setError('进度加载失败'));
  }, [page, pageSize, loadSummary]);

  const toggleEvidence = useCallback(async (w: WordSummary) => {
    if (expanded === w.wordId) {
      setExpanded(null);
      return;
    }
    setExpanded(w.wordId);
    if (evidence[w.wordId] === undefined) {
      setEvidence((prev) => ({ ...prev, [w.wordId]: [] }));
      try {
        const res = await fetch(`/api/progress/words/${w.wordId}/evidence`);
        const data = await res.json();
        setEvidence((prev) => ({ ...prev, [w.wordId]: data.items ?? [] }));
      } catch {
        setEvidence((prev) => ({ ...prev, [w.wordId]: [] }));
      }
    }
  }, [expanded, evidence]);

  const promote = useCallback(async (w: WordSummary) => {
    // POST /api/word-lists/spontaneous/import 只作用于 learning_items（已排期词）。
    // 因此本按钮的语义是“确保该词在我的学习计划中”：对已在进度里的词重复 promote 是
    // 幂等的（后端按 lemmas 查找，已存在则跳过），结果 {promoted, unknown} 仅作确认用。
    try {
      const res = await fetch('/api/word-lists/spontaneous/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lemmas: [w.lemma] }),
      });
      await res.json();
      await loadSummary(page, pageSize);
    } catch {
      // 提升失败不打断浏览；保留当前列表。
    }
  }, [page, pageSize, loadSummary]);

  if (error) return <div style={{ padding: 16 }}>进度加载失败：{error}</div>;
  if (!summary) return <div style={{ padding: 16 }}>进度加载中…（需启动 API 8000）</div>;

  const totalPages = Math.max(1, Math.ceil(summary.totalWords / summary.pageSize));
  const items = evidence[expanded ?? ''] ?? [];

  return (
    <div style={{ padding: 16, textAlign: 'left', maxWidth: 860, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h2 style={{ color: 'var(--text-h)', margin: 0 }}>学习进度</h2>
        <span
          style={{
            display: 'inline-block',
            padding: '4px 12px',
            borderRadius: 999,
            background: 'var(--accent-bg)',
            border: '1px solid var(--accent-border)',
            color: 'var(--accent)',
            fontFamily: 'var(--mono)',
            fontSize: 14,
          }}
        >
          策略 {summary.strategy.evidencePolicyVersion} · FSRS{' '}
          {fsrsVersionLabel(summary.strategy.fsrsAlgorithmVersion)}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, margin: '16px 0', flexWrap: 'wrap' }}>
        <div style={cardStyle}>今日到期 {summary.totals.dueToday}</div>
        <div style={cardStyle}>任务词 {summary.totals.quest}</div>
        <div style={cardStyle}>自由词 {summary.totals.free}</div>
      </div>

      <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
        {summary.words.length === 0 && <div style={{ padding: 16 }}>暂无词条</div>}
        {summary.words.map((w) => (
          <div key={w.wordId}>
            <div
              style={rowStyle}
              role="button"
              aria-expanded={expanded === w.wordId}
              onClick={() => toggleEvidence(w)}
            >
              <strong style={{ color: 'var(--text-h)' }}>{w.lemma}</strong>
              <span style={{ opacity: 0.75 }}>[{w.pos}] {w.ipa ?? ''}</span>
              {w.cefr && <span style={chipStyle}>{w.cefr}</span>}
              <span style={chipStyle}>{w.source}</span>
              <span style={chipStyle}>证据 {w.evidenceCount}</span>
              <span style={chipStyle} title="实验性评分轴">
                ASR 置信度代理 {w.scores.asrConfidence.toFixed(2)}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  promote(w);
                }}
              >
                提升
              </button>
            </div>
            {expanded === w.wordId && (
              <div
                style={{
                  padding: '8px 16px',
                  borderBottom: '1px solid var(--border)',
                  background: 'var(--code-bg)',
                  fontSize: 14,
                }}
              >
                {items.length > 0 ? (
                  items.map((ev) => (
                    <div key={ev.evidenceId}>
                      {ev.created_at} · {ev.axis} · {ev.result}（置信度 {ev.confidence.toFixed(2)}）
                    </div>
                  ))
                ) : (
                  <span>暂无证据</span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <div style={{ marginTop: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
        <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
          ← 上一页
        </button>
        <span>
          第 {summary.page} / {totalPages} 页（共 {summary.totalWords} 词）
        </span>
        <button onClick={() => setPage((p) => p + 1)} disabled={page >= totalPages}>
          下一页 →
        </button>
      </div>
    </div>
  );
}
