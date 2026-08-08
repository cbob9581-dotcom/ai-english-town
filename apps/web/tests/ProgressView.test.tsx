import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, it, test, expect, beforeEach, vi } from 'vitest';
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
  afterEach(() => {
    cleanup();
  });

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

  it('shows word-level ASR confidence label', async () => {
    render(<ProgressView />);
    await waitFor(() => screen.getByText(/Word-level ASR confidence/));
  });

  test('shows word-level ASR confidence label and value', async () => {
    global.fetch = vi.fn(async () => ({
      json: async () => ({
        strategy: { evidencePolicyVersion: 'v1', fsrsAlgorithmVersion: 'fsrs-5' },
        totals: { quest: 1, free: 0, dueToday: 0 },
        page: 1, pageSize: 50, totalWords: 1,
        words: [{
          wordId: 'w1', lemma: 'loaf', pos: 'n', ipa: '/loʊf/', cefr: 'A1',
          sceneTags: [], source: 'quest', carrier: 'object',
          scores: { productive: 0.6, receptive: 0.5, asrConfidence: 0.0, asrWordConfidence: 0.95 },
          fsrs: { state: 'learning', due: null, reps: 0, lapses: 0 },
          evidenceCount: 1, lastEvidenceAt: null,
        }],
      }),
    }) as any);
    render(<ProgressView />);
    expect(await screen.findByText(/Word-level ASR confidence/i)).toBeInTheDocument();
    expect(await screen.findByText(/0\.95/)).toBeInTheDocument();
  });

  test('marks word-level evidence rows in detail', async () => {
    const evRows = [{ evidence_id: 'e1', source: 'word_production', axis: 'asr_word_confidence',
                      result: 'success', confidence: 0.88, created_at: '2026-08-08T12:00:00Z' }];
    global.fetch = vi.fn(async (url: string) => ({
      json: async () => url.includes('/evidence')
        ? { items: evRows }
        : { strategy: { evidencePolicyVersion: 'v1', fsrsAlgorithmVersion: 'fsrs-5' },
            totals: { quest: 0, free: 0, dueToday: 0 }, page: 1, pageSize: 50, totalWords: 1,
            words: [{ wordId: 'w1', lemma: 'loaf', pos: 'n', ipa: null, cefr: null,
                      sceneTags: [], source: 'quest', carrier: null,
                      scores: { productive: 0, receptive: 0, asrConfidence: 0, asrWordConfidence: 0.88 },
                      fsrs: { state: 'learning', due: null, reps: 0, lapses: 0 },
                      evidenceCount: 1, lastEvidenceAt: null }] },
    }) as any);
    render(<ProgressView />);
    const row = await screen.findByText(/loaf/);
    row.click();
    expect(await screen.findByText(/词级 · 0\.88/)).toBeInTheDocument();
  });
});
