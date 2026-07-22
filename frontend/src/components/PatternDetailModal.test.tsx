import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PatternDetailModal from './PatternDetailModal';
import type { AdPattern } from '../api/patterns';

const mockSplitPattern = vi.fn();
const mockUpdatePattern = vi.fn();
const mockDeletePattern = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  splitPattern: (...a: unknown[]) => mockSplitPattern(...a),
  updatePattern: (...a: unknown[]) => mockUpdatePattern(...a),
  deletePattern: (...a: unknown[]) => mockDeletePattern(...a),
}));

vi.mock('../api/sponsors', () => ({
  getSponsors: vi.fn().mockResolvedValue([]),
  addSponsor: vi.fn(),
}));

function makePattern(overrides: Partial<AdPattern> = {}): AdPattern {
  return {
    id: 42,
    scope: 'podcast',
    network_id: null,
    podcast_id: 'show-a',
    dai_platform: null,
    text_template: 'This episode is brought to you by Acme.',
    intro_variants: '[]',
    outro_variants: '[]',
    sponsor: 'Acme',
    confirmation_count: 3,
    false_positive_count: 0,
    last_matched_at: null,
    created_at: '2026-01-01T00:00:00Z',
    created_from_episode_id: null,
    is_active: true,
    disabled_at: null,
    disabled_reason: null,
    ...overrides,
  };
}

function renderModal(pattern: AdPattern, onClose = vi.fn(), onSave = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <PatternDetailModal pattern={pattern} onClose={onClose} onSave={onSave} />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Split button', () => {
  it('renders for an active pattern', () => {
    renderModal(makePattern({ is_active: true }));
    expect(screen.getByRole('button', { name: 'Split' })).toBeDefined();
  });

  it('does not render for an inactive pattern', () => {
    renderModal(makePattern({ is_active: false }));
    expect(screen.queryByRole('button', { name: 'Split' })).toBeNull();
  });

  it('splits successfully, invalidates the patterns query, and closes', async () => {
    mockSplitPattern.mockResolvedValue({
      success: true, original_pattern_id: 42, new_pattern_ids: [43, 44],
      message: 'Split into 2 patterns',
    });
    const onClose = vi.fn();
    const { invalidateSpy } = renderModal(makePattern(), onClose);

    await userEvent.click(screen.getByRole('button', { name: 'Split' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockSplitPattern).toHaveBeenCalledWith(42);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
  });

  it('disables the button while the mutation is pending', async () => {
    let resolveSplit: (v: unknown) => void = () => {};
    mockSplitPattern.mockReturnValue(new Promise((resolve) => { resolveSplit = resolve; }));
    renderModal(makePattern());

    const button = screen.getByRole('button', { name: 'Split' });
    await userEvent.click(button);

    await waitFor(() => expect(
      screen.getByRole('button', { name: 'Splitting...' }),
    ).toHaveProperty('disabled', true));
    resolveSplit({ success: true, original_pattern_id: 42, new_pattern_ids: [43], message: 'ok' });
  });

  it('shows the API error text on a 400 (no split points) without crashing', async () => {
    mockSplitPattern.mockRejectedValue(new Error('Pattern 42 not found or has nothing to split'));
    renderModal(makePattern());

    await userEvent.click(screen.getByRole('button', { name: 'Split' }));

    await waitFor(() => expect(
      screen.getByText('Pattern 42 not found or has nothing to split'),
    ).toBeDefined());
    // Component stays mounted and interactive after the error.
    expect(screen.getByRole('button', { name: 'Split' })).toBeDefined();
  });
});
