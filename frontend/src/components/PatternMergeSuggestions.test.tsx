import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import PatternMergeSuggestions from './PatternMergeSuggestions';

vi.mock('../api/patterns', () => ({
  getMergeSuggestions: vi.fn().mockResolvedValue([{
    sponsor_id: 1, sponsor: 'Acme', suggested_keep_id: 1, pattern_ids: [1, 2],
    count: 2, result_intro_variant_count: 1, result_outro_variant_count: 0,
    members: [
      { id: 1, text_template: 'first template' },
      { id: 2, text_template: 'second template' },
    ],
  }]),
  mergePatterns: vi.fn(),
}));

describe('PatternMergeSuggestions', () => {
  it('puts the merge button in a footer after the member list', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <PatternMergeSuggestions onMerged={() => {}} />
      </QueryClientProvider>,
    );
    const button = await screen.findByRole('button', { name: 'Merge 2 into 1' });
    const list = screen.getByRole('list');
    expect(list.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(button.parentElement?.className).toContain('sm:justify-end');
    expect(button.className).toContain('max-sm:w-full');
  });
});
