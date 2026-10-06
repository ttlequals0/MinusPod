import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import CleanupTab from './CleanupTab';
import { splitTrim } from './TrimDiff';
import { ApiError } from '../../api/client';
import type { PatternCleanupSuggestion } from '../../api/patternCleanup';

const mockList = vi.fn();
const mockApprove = vi.fn();
const mockReject = vi.fn();
const mockUndo = vi.fn();
const mockBulk = vi.fn();

vi.mock('../../api/patternCleanup', () => ({
  patternCleanupQueryKey: ['patternCleanup'],
  getPatternCleanupSuggestions: (...a: unknown[]) => mockList(...a),
  approveCleanupSuggestion: (...a: unknown[]) => mockApprove(...a),
  rejectCleanupSuggestion: (...a: unknown[]) => mockReject(...a),
  undoCleanupSuggestion: (...a: unknown[]) => mockUndo(...a),
  bulkCleanupSuggestions: (...a: unknown[]) => mockBulk(...a),
}));

const ORIGINAL = 'and we are back after this. This episode is brought to you by Acme, visit acme dot com. Okay so anyway';
const KEPT = 'This episode is brought to you by Acme, visit acme dot com.';

function suggestion(
  id: number,
  kind: PatternCleanupSuggestion['kind'],
  payload: PatternCleanupSuggestion['payload'],
  overrides: Partial<PatternCleanupSuggestion> = {},
): PatternCleanupSuggestion {
  return {
    id, runId: 1, patternId: 100 + id, kind, status: 'pending', confidence: 0.82,
    reasons: [`reason for ${kind}`], payload,
    before: {
      textTemplate: ORIGINAL, sponsor: 'Acme', introVariants: [], outroVariants: [],
      isActive: true, disabledReason: null,
    },
    applied: null, createdAt: '2026-10-05T04:00:00Z', reviewedAt: null,
    pattern: {
      id: 100 + id, sponsor: 'Acme', scope: 'podcast', networkId: null,
      podcastTitle: 'The Daily Tech Show', isActive: true, confirmationCount: 7,
      falsePositiveCount: 1, lastMatchedAt: '2026-06-01T00:00:00Z',
      createdAt: '2026-01-01T00:00:00Z',
    },
    ...overrides,
  };
}

const ALL_KINDS = [
  suggestion(1, 'trim', { text: KEPT }),
  suggestion(2, 'split', { pieces: [
    { text: 'Brought to you by Acme.', sponsor: 'Acme' },
    { text: 'Also by Widgetco.', sponsor: 'Widgetco' },
  ] }),
  suggestion(3, 'rename', { sponsor: 'Acme Corp' }),
  suggestion(4, 'retire', { unusedDays: 120, lastMatchedAt: '2026-06-01T00:00:00Z', confirmationCount: 7 }),
  suggestion(5, 'flag', {
    falsePositiveCount: 4, confirmationCount: 1, contaminated: true,
    contaminationReason: 'Contains show banter', recommended: 'disable',
  }),
];

function renderTab() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CleanupTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function card(id: number) {
  return screen.getByTestId(`cleanup-suggestion-${id}`);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue(ALL_KINDS);
  mockApprove.mockImplementation(async (id: number) => ({ ...ALL_KINDS[0], id, status: 'approved' }));
  mockReject.mockImplementation(async (id: number) => ({ ...ALL_KINDS[0], id, status: 'rejected' }));
  mockUndo.mockImplementation(async (id: number) => ({ ...ALL_KINDS[0], id, status: 'undone' }));
  mockBulk.mockResolvedValue([]);
});

describe('splitTrim', () => {
  it('locates the kept copy inside the original', () => {
    expect(splitTrim(ORIGINAL, KEPT)).toEqual({
      before: 'and we are back after this. ',
      kept: KEPT,
      after: ' Okay so anyway',
    });
  });

  it('matches across case and whitespace differences', () => {
    const parts = splitTrim('Intro words.  THIS is   the ad copy. Outro', 'this is the ad copy.');
    expect(parts?.before).toBe('Intro words.  ');
    expect(parts?.kept).toBe('THIS is   the ad copy.');
    expect(parts?.after).toBe(' Outro');
  });

  it('returns null when the kept copy is not in the original', () => {
    expect(splitTrim('one two three', 'four five')).toBeNull();
  });
});

describe('CleanupTab', () => {
  it('loads pending suggestions by default', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-1');
    expect(mockList).toHaveBeenCalledWith(expect.objectContaining({ status: 'pending' }));
    expect(mockList.mock.lastCall?.[0].kind).toBeUndefined();
  });

  it('renders the trim as struck removed text and highlighted kept copy', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-1');
    const c = card(1);
    const removed = c.querySelectorAll('del');
    expect(Array.from(removed).map((d) => d.textContent)).toEqual([
      'and we are back after this. ', ' Okay so anyway',
    ]);
    expect(c.querySelector('[data-diff="kept"]')?.textContent).toBe(KEPT);
    expect(within(c).getByText('Trim')).toBeDefined();
    expect(within(c).getByText('The Daily Tech Show')).toBeDefined();
  });

  it('makes original pattern text expandable for every suggestion kind', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-1');
    for (const item of ALL_KINDS) {
      const disclosure = within(card(item.id)).getByText('Original pattern text');
      expect(disclosure.closest('details')?.querySelector('p')?.textContent).toBe(ORIGINAL);
    }
  });

  it('shows retained source context when the API provides it', async () => {
    mockList.mockResolvedValue([suggestion(1, 'split', { pieces: [] }, {
      before: {
        textTemplate: ORIGINAL, sourceContext: 'Transcript before and after the pattern', sponsor: 'Acme',
        introVariants: [], outroVariants: [], isActive: true, disabledReason: null,
      },
    })]);
    renderTab();
    const context = await screen.findByText('Source context');
    expect(context.closest('details')?.querySelector('p')?.textContent)
      .toBe('Transcript before and after the pattern');
  });

  it('renders a combined trim and sponsor rename', async () => {
    mockList.mockResolvedValue([suggestion(1, 'trim', { text: KEPT, sponsor: 'Acme Group' })]);
    renderTab();
    const c = await screen.findByTestId('cleanup-suggestion-1');
    expect(within(c).getByText('Trim and rename')).toBeDefined();
    expect(within(c).getByText('Acme Group')).toBeDefined();
    expect(c.querySelector('[data-diff="kept"]')?.textContent).toBe(KEPT);
  });

  it('renders sponsor rename on a trim recommendation from a flag', async () => {
    mockList.mockResolvedValue([suggestion(1, 'flag', {
      falsePositiveCount: 4, confirmationCount: 1, contaminated: false,
      contaminationReason: null, recommended: 'trim', trimText: KEPT, sponsor: 'Acme Group',
    })]);
    renderTab();
    const c = await screen.findByTestId('cleanup-suggestion-1');
    expect(within(c).getByText('Trim and rename')).toBeDefined();
    expect(within(c).getByText('Acme Group')).toBeDefined();
    expect(c.querySelector('[data-diff="kept"]')?.textContent).toBe(KEPT);
  });

  it('loads an older approved suggestion, allows undo, then refetches both pages', async () => {
    const recent = Array.from({ length: 200 }, (_, i) => suggestion(200 - i, 'retire', {
      unusedDays: 90, lastMatchedAt: null, confirmationCount: 0,
    }, { status: 'approved' }));
    const older = suggestion(0, 'trim', { text: KEPT }, { status: 'approved' });
    let undone = false;
    mockList.mockImplementation(({ beforeId }: { beforeId?: number }) =>
      Promise.resolve(beforeId === undefined ? recent : undone ? [] : [older]));
    renderTab();
    await screen.findByTestId('cleanup-suggestion-200');
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText('Status'), 'approved');
    await user.click(screen.getByRole('button', { name: 'Load older suggestions' }));
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 200, beforeId: 1 })));
    const olderCard = await screen.findByTestId('cleanup-suggestion-0');
    expect(within(olderCard).getByRole('button', { name: 'Undo' })).toBeDefined();
    mockList.mockClear();
    mockUndo.mockImplementationOnce(async () => {
      undone = true;
      return { ...older, status: 'undone' };
    });
    await user.click(within(olderCard).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(mockUndo).toHaveBeenCalledWith(0));
    await waitFor(() => expect(mockList.mock.calls.some(([params]) => params.beforeId === undefined)).toBe(true));
    await waitFor(() => expect(mockList.mock.calls.some(([params]) => params.beforeId === 1)).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('cleanup-suggestion-0')).toBeNull());
  });

  it('pages with a before_id cursor and de-duplicates any row returned twice', async () => {
    const page1 = Array.from({ length: 200 }, (_, i) => suggestion(400 - i, 'retire', {
      unusedDays: 90, lastMatchedAt: null, confirmationCount: 0,
    }));
    // A stale offset cursor could re-return page 1's last row; the cursor is a real id,
    // so this is a defensive dedupe, not something the paging itself should produce.
    const page2 = [page1[199], ...Array.from({ length: 5 }, (_, i) => suggestion(200 - i, 'retire', {
      unusedDays: 90, lastMatchedAt: null, confirmationCount: 0,
    }))];
    mockList.mockImplementation(({ beforeId }: { beforeId?: number }) =>
      Promise.resolve(beforeId === undefined ? page1 : page2));
    renderTab();
    await screen.findByTestId('cleanup-suggestion-400');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Load older suggestions' }));
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({ beforeId: 201 })));
    await screen.findByTestId('cleanup-suggestion-196');
    expect(screen.getAllByTestId('cleanup-suggestion-201')).toHaveLength(1);
  });

  it('renders each split piece with its sponsor', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-2');
    const pieces = within(card(2)).getAllByTestId('split-piece');
    expect(pieces).toHaveLength(2);
    expect(within(pieces[1]).getByText('Widgetco')).toBeDefined();
    expect(within(pieces[1]).getAllByText('Also by Widgetco.')).toHaveLength(2);
    const user = userEvent.setup();
    await user.click(within(pieces[0]).getByText('Locate piece in original text'));
    expect(within(pieces[0]).getByText('Brought to you by Acme.')).toBeDefined();
    expect(pieces[0].querySelectorAll('del').length).toBeGreaterThan(0);
  });

  it('renders rename, retire and flag details', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-3');
    expect(within(card(3)).getByText('Acme Corp')).toBeDefined();
    expect(within(card(4)).getByText(/No matches in 120 days/)).toBeDefined();
    const flag = card(5);
    expect(within(flag).getByText('Contains show banter')).toBeDefined();
    expect(within(flag).getByText(/4 false positives/)).toBeDefined();
    expect(within(flag).getByText(/Disable the pattern/)).toBeDefined();
    expect(within(flag).getByText('reason for flag')).toBeDefined();
  });

  it('filters by kind', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.selectOptions(document.getElementById('cleanup-kind')!, 'split');
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: 'split', status: 'pending' })));
  });

  it('filters by status', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.selectOptions(screen.getByLabelText('Status'), 'approved');
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'approved' })));
  });

  it('approves and rejects one suggestion', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.click(within(card(1)).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(mockApprove).toHaveBeenCalledWith(1));
    await user.click(within(card(2)).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(mockReject).toHaveBeenCalledWith(2));
  });

  it('offers Undo instead of Approve and Reject on an approved suggestion', async () => {
    mockList.mockResolvedValue([suggestion(1, 'trim', { text: KEPT }, {
      status: 'approved', reviewedAt: '2026-10-05T05:00:00Z',
    })]);
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    expect(within(card(1)).queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(within(card(1)).queryByRole('button', { name: 'Reject' })).toBeNull();
    expect(screen.queryByLabelText('Select all')).toBeNull();
    await user.click(within(card(1)).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(mockUndo).toHaveBeenCalledWith(1));
  });

  it('shows a conflict message when an action returns 409', async () => {
    mockApprove.mockRejectedValue(new ApiError('invalid_transition', 409));
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.click(within(card(1)).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText(/pattern changed or the suggestion was already handled/))
      .toBeDefined();
  });

  it('explains a refused undo', async () => {
    mockList.mockResolvedValue([suggestion(1, 'trim', { text: KEPT }, { status: 'approved' })]);
    mockUndo.mockRejectedValue(new ApiError('invalid_transition', 409));
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.click(within(card(1)).getByRole('button', { name: 'Undo' }));
    expect(await screen.findByText(
      'Undo is unavailable because the pattern changed, a later approval remains, or the saved state is incomplete.',
    )).toBeDefined();
  });

  it('selects all and bulk approves', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.click(screen.getByLabelText('Select all'));
    await user.click(screen.getByRole('button', { name: 'Approve selected (5)' }));
    await waitFor(() => expect(mockBulk).toHaveBeenCalledWith([1, 2, 3, 4, 5], 'approve'));
  });

  it('bulk rejects only the checked suggestions', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.click(within(card(2)).getByLabelText('Select suggestion 2'));
    await user.click(within(card(4)).getByLabelText('Select suggestion 4'));
    await user.click(screen.getByRole('button', { name: 'Reject selected (2)' }));
    await waitFor(() => expect(mockBulk).toHaveBeenCalledWith([2, 4], 'reject'));
  });

  it('scopes the card checkbox 44px tap target to phones, not desktop', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-1');
    const checkbox = within(card(2)).getByLabelText('Select suggestion 2');
    const tokens = checkbox.parentElement!.className.split(' ');
    expect(tokens).toContain('max-sm:min-h-11');
    expect(tokens).toContain('max-sm:min-w-11');
    expect(tokens).not.toContain('min-h-11');
    expect(tokens).not.toContain('min-w-11');
  });

  it('reports bulk items that could not be applied', async () => {
    mockBulk.mockResolvedValue([
      { id: 1, status: 'approved' }, { id: 2, error: 'invalid_transition' },
    ]);
    renderTab();
    const user = userEvent.setup();
    await screen.findByTestId('cleanup-suggestion-1');
    await user.click(within(card(1)).getByLabelText('Select suggestion 1'));
    await user.click(within(card(2)).getByLabelText('Select suggestion 2'));
    await user.click(screen.getByRole('button', { name: 'Approve selected (2)' }));
    expect(await screen.findByText(/1 of 2 suggestions could not be applied/)).toBeDefined();
  });

  it('shows the empty state with a link to Settings', async () => {
    mockList.mockResolvedValue([]);
    renderTab();
    expect(await screen.findByText(/No suggestions\./)).toBeDefined();
    const link = screen.getByRole('link', { name: 'Settings > Experiments' });
    expect(link.getAttribute('href')).toBe('/settings');
  });
});
