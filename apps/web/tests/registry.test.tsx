import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { renderEntity } from '../src/registry';

describe('registry', () => {
  it('renders a prop as a button with its label', () => {
    const entity = {
      id: 'loaf-1', component: 'prop',
      layout: { x: 0, y: 0, w: 100, h: 100, anchor: 'bottom' },
      appearance: { visualKey: 'food.loaf' },
      semantics: { name: 'loaf', wordId: 'word_loaf_n_1' },
      interactions: ['ask'],
    };
    render(<div>{renderEntity(entity)}</div>);
    expect(screen.getByRole('button', { name: /loaf/i })).toBeInTheDocument();
  });

  it('renders null for unknown component (whitelist)', () => {
    const entity: any = { id: 'x', component: 'script', layout: { x: 0, y: 0, w: 1, h: 1, anchor: 'bottom' }, appearance: { visualKey: 'a.b' }, semantics: { name: 'x' } };
    expect(renderEntity(entity)).toBeNull();
  });
});
