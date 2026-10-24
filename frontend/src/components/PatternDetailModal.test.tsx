import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
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
    can_split: true,
    disabled_at: null,
    disabled_reason: null,
    ...overrides,
  };
}

function renderModal(pattern: AdPattern, onClose = vi.fn(), onSave = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <PatternDetailModal pattern={pattern} onClose={onClose} onSave={onSave} />
      </QueryClientProvider>
    </MemoryRouter>,
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

  it('explains why an active pattern cannot be split', async () => {
    renderModal(makePattern({ can_split: false }));
    const button = screen.getByRole('button', { name: 'Split' });
    expect(button).toHaveProperty('disabled', true);
    expect(screen.getByText('No reliable ad boundary found.')).toBeDefined();
    await userEvent.click(button);
    expect(mockSplitPattern).not.toHaveBeenCalled();
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

describe('Category editing', () => {
  it('shows a category select preloaded with the stored category', async () => {
    renderModal(makePattern({ category: 'cross_promo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));

    const select = screen.getByLabelText('Category') as HTMLSelectElement;
    expect(select.value).toBe('cross_promo');
  });

  it('saves a changed category through updatePattern', async () => {
    mockUpdatePattern.mockResolvedValue(undefined);
    renderModal(makePattern({ category: 'cross_promo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));

    await userEvent.selectOptions(screen.getByLabelText('Category'), 'sponsor');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdatePattern).toHaveBeenCalledWith(
      42, expect.objectContaining({ category: 'sponsor' }),
    ));
  });

  it('clearing the category saves null so the backend unsets it', async () => {
    mockUpdatePattern.mockResolvedValue(undefined);
    renderModal(makePattern({ category: 'cross_promo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));

    await userEvent.selectOptions(screen.getByLabelText('Category'), '');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdatePattern).toHaveBeenCalledWith(
      42, expect.objectContaining({ category: null }),
    ));
  });
});

describe('Footer actions', () => {
  it('shows Split, Delete, Close and Edit as text buttons at every width', () => {
    renderModal(makePattern());
    for (const name of ['Split', 'Delete', 'Close', 'Edit']) {
      const button = screen.getByRole('button', { name });
      expect(button.textContent).toBe(name);
      expect(button.querySelector('svg')).toBeNull();
    }
  });

  it('confirms delete through the Delete pattern and Cancel buttons', async () => {
    mockDeletePattern.mockResolvedValue(undefined);
    const onClose = vi.fn();
    renderModal(makePattern(), onClose);
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Delete pattern' }));
    await waitFor(() => expect(mockDeletePattern).toHaveBeenCalledWith(42));
  });
});

describe('podcast link', () => {
  it('links to the feed page, not the API episodes path', () => {
    renderModal(makePattern({ podcast_slug: 'example-podcast' }));
    expect(screen.getByRole('link', { name: 'example-podcast' }).getAttribute('href')).toBe('/feeds/example-podcast');
  });
});
