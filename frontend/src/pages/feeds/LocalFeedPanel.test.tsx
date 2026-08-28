/**
 * Component tests for LocalFeedPanel.tsx (#625 Task 13).
 *
 * Covers:
 *   - Renders for a local feed (title, Add episode button, Bulk import section).
 *   - A scanned import plan renders matched entries (via ImportPreviewTable)
 *     plus rejected files from the upload step.
 *   - Confirming the scanned plan posts importCommit with the planHash the
 *     scan returned.
 *   - The artwork warning row shows when the feed has no artworkUrl, and is
 *     absent when it does.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LocalFeedPanel from './LocalFeedPanel';
import type { Feed } from '../../api/types';
import type { ImportPlan, ImportUploadResult, ImportStatus } from '../../api/feeds';

// CollapsibleSection defaults closed; render children unconditionally so the
// panel's contents are queryable without simulating a click first (mirrors
// FeedSettingsPanel.test.tsx's unwrap pattern).
vi.mock('../../components/CollapsibleSection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components/CollapsibleSection')>();
  return {
    useCollapsibleOpen: actual.useCollapsibleOpen,
    default: ({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) => (
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
        {children}
      </div>
    ),
  };
});

const mockUpdateFeed = vi.fn();
const mockUploadFeedArtwork = vi.fn();
const mockUploadLocalEpisode = vi.fn();
const mockImportUpload = vi.fn();
const mockImportScan = vi.fn();
const mockImportCommit = vi.fn();
const mockImportStatus = vi.fn();

vi.mock('../../api/feeds', () => ({
  updateFeed: (...args: unknown[]) => mockUpdateFeed(...args),
  uploadFeedArtwork: (...args: unknown[]) => mockUploadFeedArtwork(...args),
  uploadLocalEpisode: (...args: unknown[]) => mockUploadLocalEpisode(...args),
  importUpload: (...args: unknown[]) => mockImportUpload(...args),
  importScan: (...args: unknown[]) => mockImportScan(...args),
  importCommit: (...args: unknown[]) => mockImportCommit(...args),
  importStatus: (...args: unknown[]) => mockImportStatus(...args),
}));

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    slug: 'archive-show',
    title: 'Archive Show',
    sourceUrl: '',
    feedUrl: 'https://example.com/archive-show.xml',
    feedType: 'local',
    episodeCount: 2,
    artworkUrl: 'https://example.com/art.jpg',
    ...overrides,
  };
}

function makePlan(overrides: Partial<ImportPlan> = {}): ImportPlan {
  return {
    slug: 'archive-show',
    overwrite: false,
    planHash: 'hash-abc123',
    entries: [
      {
        episodeId: 's01e01',
        season: 1,
        episode: 1,
        title: 'The Beginning',
        audioFile: 'S01E01 - The Beginning.mp3',
        descriptionFile: 'S01E01 - The Beginning.txt',
        artworkFile: null,
        sidecarFile: null,
        publishedAt: '2026-01-01T00:00:00Z',
        publishedAtSource: 'explicit',
        bytes: 1024,
        mtimeNs: 0,
        warnings: [],
        errors: [],
      },
    ],
    rejected: [{ file: 'stray.wav', reason: 'not a supported audio format (only .mp3 is imported)' }],
    totals: { importable: 1, rejected: 1, errors: 0, bytes: 1024 },
    ...overrides,
  };
}

function renderPanel(feed: Feed) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LocalFeedPanel feed={feed} slug={feed.slug} />
    </QueryClientProvider>,
  );
}

const IDLE_STATUS: ImportStatus = { state: 'idle', processed: 0, total: 0, startedAt: null };

describe('LocalFeedPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockImportStatus.mockResolvedValue(IDLE_STATUS);
    // The artwork-missing warning HEADs the feed's own artwork URL directly
    // (feed.artworkUrl is always a URL, present or not -- see the query's
    // comment in LocalFeedPanel.tsx) rather than going through apiRequest.
    // Default to "has artwork" (ok: true); individual tests override this.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
  });

  it('renders for a local feed', async () => {
    renderPanel(makeFeed());

    expect(screen.getByText('Local feed')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Add episode' })).toBeDefined();
    expect(screen.getByText('Bulk import')).toBeDefined();
    await waitFor(() => expect(mockImportStatus).toHaveBeenCalledWith('archive-show'));
  });

  it('renders matched and rejected rows from a scan result', async () => {
    const user = userEvent.setup();
    const uploadResult: ImportUploadResult = {
      staged: ['S01E01 - The Beginning.mp3'],
      rejected: [{ file: 'bad.flac', reason: 'not a supported audio format (only .mp3 is imported)' }],
    };
    mockImportUpload.mockResolvedValue(uploadResult);
    mockImportScan.mockResolvedValue(makePlan());
    const { container } = renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    const file = new File(['x'], 'S01E01 - The Beginning.mp3', { type: 'audio/mpeg' });
    await user.upload(fileInput, file);

    await waitFor(() => expect(mockImportScan).toHaveBeenCalledWith('archive-show', { source: 'staging' }));

    // Matched entry from the scan plan, scoped to the desktop table (the
    // sm:hidden mobile card duplicates the same row in jsdom, which has no
    // CSS to actually hide it).
    await waitFor(() => expect(container.querySelector('table')).not.toBeNull());
    const table = within(container.querySelector('table') as HTMLTableElement);
    expect(table.getByText('The Beginning')).toBeDefined();
    expect(table.getByText('s01e01')).toBeDefined();

    // Rejected file surfaced from the scan plan's own rejected list.
    expect(screen.getByText('stray.wav')).toBeDefined();
    expect(screen.getByText(/not a supported audio format/)).toBeDefined();
  });

  it('posts the scanned planHash when the plan is confirmed', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    mockImportScan.mockResolvedValue(makePlan());
    mockImportCommit.mockResolvedValue({ message: 'import started' });
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    const confirmButton = screen.getByRole('button', { name: /Import 1 episode/ });
    await user.click(confirmButton);

    await waitFor(() => expect(mockImportCommit).toHaveBeenCalledWith('archive-show', {
      planHash: 'hash-abc123',
      source: 'staging',
    }));
  });

  it('shows a warning when the feed has no artwork', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    renderPanel(makeFeed());
    expect(await screen.findByText(/No artwork uploaded/)).toBeDefined();
  });

  it('hides the artwork warning once the feed has artwork', async () => {
    renderPanel(makeFeed({ artworkUrl: 'https://example.com/art.jpg' }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByText(/No artwork uploaded/)).toBeNull();
  });
});
