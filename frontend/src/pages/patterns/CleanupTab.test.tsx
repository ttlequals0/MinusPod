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

  it('renders each split piece with its sponsor', async () => {
    renderTab();
    await screen.findByTestId('cleanup-suggestion-2');
    const pieces = within(card(2)).getAllByTestId('split-piece');
    expect(pieces).toHaveLength(2);
    expect(within(pieces[1]).getByText('Widgetco')).toBeDefined();
    expect(within(pieces[1]).getByText('Also by Widgetco.')).toBeDefined();
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
    await user.click(screen.getByRole('button', { name: 'Split' }));
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
