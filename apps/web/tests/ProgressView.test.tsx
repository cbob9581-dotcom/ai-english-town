import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import ProgressView from '../src/ProgressView';

const SUMMARY = {
  strategy: { evidencePolicyVersion: 'v1', fsrsAlgorithmVersion: 'fsrs-5' },
  totals: { quest: 1, free: 1, dueToday: 1 },
  page: 1, pageSize: 50, totalWords: 2,
  words: [
    { wordId: 'word_loaf_n_1', lemma: 'loaf', pos: 'n', ipa: '/loʊf/', cefr: 'A2',
      sceneTags: ['bakery'], source: 'quest', carrier: 'object',
      scores: { productive: 0.7, receptive: 0.5, asrConfidence: 0.6 },
      fsrs: { state: 'review', due: '2026-08-09T00:00:00Z', reps: 2, lapses: 0 },
      evidenceCount: 3, lastEvidenceAt: null },
  ],
};

describe('ProgressView', () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/summary')) return Promise.resolve({ json: () => Promise.resolve(SUMMARY) } as any);
      return Promise.resolve({ json: () => Promise.resolve({ items: [] }) } as any);
    });
  });

  it('renders strategy badge and totals', async () => {
    render(<ProgressView />);
    await waitFor(() => screen.getByText(/策略 v1 · FSRS v5/));
    expect(screen.getByText(/loaf/)).toBeInTheDocument();
    expect(screen.getByText(/今日到期 1/)).toBeInTheDocument();
  });

  it('shows experimental label for ASR confidence axis', async () => {
    render(<ProgressView />);
    await waitFor(() => screen.getByText(/ASR 置信度代理/));
  });
});
