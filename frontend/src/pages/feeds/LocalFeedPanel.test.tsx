/**
 * Component tests for LocalFeedPanel.tsx (#625 Task 13).
 *
 * Covers:
 *   - Renders for a local feed (title, Add episode button, Bulk import section).
 *   - A scanned import plan renders matched entries (via ImportPreviewTable)
 *     plus rejected files from BOTH the upload step and the scan's own
 *     rejected list.
 *   - Confirming the scanned plan posts importCommit with the planHash the
 *     scan returned.
 *   - The artwork warning row shows when feed.hasArtwork is false, and is
 *     absent when it's true.
 *   - Saving metadata sends null (not omitted) for a blanked author/
 *     categories, and always sends p20.locked_owner so a blank one clears.
 *   - Podcasting 2.0 list tag editors (funding/person/license/location/txt/
 *     podroll): rows seed from feed.p20, an added+filled row is sent in the
 *     PATCH payload, removing the last row of a tag sends [], a funding row
 *     without a url (or a podroll row without a feed GUID, or a license/
 *     location/text row without a name/value) blocks save with an inline
 *     message instead of a request, and the scalar p20 keys still ride
 *     along in the payload.
 *   - Podroll's medium field renders as a select (parity with the channel
 *     medium select) and itemGuid rides along in the payload; a malformed
 *     feedGuid or more than 50 rows blocks save with its own inline message.
 *   - The "Podcasting 2.0 tags" section (CollapsibleSection.test.tsx covers
 *     its real collapse/forceOpen mechanics; this file's stub always renders
 *     children) forces open via the forceOpen prop when a validation error
 *     is set, and the error itself renders outside the section entirely.
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
// FeedSettingsPanel.test.tsx's unwrap pattern). Real collapse/forceOpen
// mechanics (does forceOpen actually reveal clipped content) are covered by
// CollapsibleSection.test.tsx against the real component, not this stub --
// this mock surfaces the forceOpen prop it was passed as a data attribute
// purely so a test here can assert LocalFeedPanel wires it correctly,
// without claiming that proves visibility on its own.
vi.mock('../../components/CollapsibleSection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components/CollapsibleSection')>();
  return {
    useCollapsibleOpen: actual.useCollapsibleOpen,
    default: ({ title, subtitle, children, forceOpen }: { title: string; subtitle?: string; children: React.ReactNode; forceOpen?: boolean }) => (
      <div data-force-open={forceOpen ? 'true' : 'false'}>
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
const mockClearImportStaging = vi.fn();

vi.mock('../../api/feeds', () => ({
  updateFeed: (...args: unknown[]) => mockUpdateFeed(...args),
  uploadFeedArtwork: (...args: unknown[]) => mockUploadFeedArtwork(...args),
  uploadLocalEpisode: (...args: unknown[]) => mockUploadLocalEpisode(...args),
  importUpload: (...args: unknown[]) => mockImportUpload(...args),
  importScan: (...args: unknown[]) => mockImportScan(...args),
  importCommit: (...args: unknown[]) => mockImportCommit(...args),
  importStatus: (...args: unknown[]) => mockImportStatus(...args),
  clearImportStaging: (...args: unknown[]) => mockClearImportStaging(...args),
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
        replacesExisting: false,
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
    mockClearImportStaging.mockResolvedValue({ message: 'staging cleared' });
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

    await waitFor(() => expect(mockImportScan).toHaveBeenCalledWith('archive-show', { source: 'staging', overwrite: false }));

    // Matched entry from the scan plan, scoped to the desktop table (the
    // sm:hidden mobile card duplicates the same row in jsdom, which has no
    // CSS to actually hide it).
    await waitFor(() => expect(container.querySelector('table')).not.toBeNull());
    const table = within(container.querySelector('table') as HTMLTableElement);
    expect(table.getByText('The Beginning')).toBeDefined();
    expect(table.getByText('s01e01')).toBeDefined();

    // Rejected file surfaced from the scan plan's own rejected list.
    expect(screen.getByText('stray.wav')).toBeDefined();
    expect(screen.getAllByText(/not a supported audio format/).length).toBeGreaterThan(0);

    // Rejected file from the upload step must ALSO stay visible now that a
    // plan has rendered -- it used to only show while `!plan`, which never
    // held true once uploadAndScanMutation's onSuccess set both uploadRejected
    // and plan in the same tick.
    expect(screen.getByText('bad.flac')).toBeDefined();
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
      overwrite: false,
    }));
  });

  it('shows a warning when the feed has no artwork', () => {
    renderPanel(makeFeed({ hasArtwork: false }));
    expect(screen.getByText(/No artwork uploaded/)).toBeDefined();
  });

  it('hides the artwork warning once the feed has artwork', () => {
    renderPanel(makeFeed({ hasArtwork: true }));
    expect(screen.queryByText(/No artwork uploaded/)).toBeNull();
  });

  it('sends null for a blanked author/categories and always sends p20.locked_owner on save', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({
      author: 'Archive Curator',
      categories: ['History'],
      p20: { medium: 'podcast', locked: 'yes', locked_owner: 'owner@example.com' },
    }));

    // Blank every clearable field, then save: sending `undefined` here would
    // drop the key from the JSON body and the backend would leave the old
    // value untouched (#625 Task 13 review finding 2).
    await user.clear(screen.getByLabelText('Author'));
    await user.clear(screen.getByLabelText('Categories'));
    await user.clear(screen.getByLabelText('Locked owner email'));
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.author).toBeNull();
    expect(payload.categories).toBeNull();
    expect(payload.p20.locked_owner).toBe('');
  });

  it('seeds Podcasting 2.0 tag rows from feed.p20', () => {
    renderPanel(makeFeed({
      p20: {
        medium: 'podcast',
        locked: 'yes',
        locked_owner: '',
        funding: [{ text: 'Support us', url: 'https://patreon.com/example' }],
        person: [{ text: 'Jane Doe', role: 'host' }],
      },
    }));

    expect(screen.getByDisplayValue('Support us')).toBeDefined();
    expect(screen.getByDisplayValue('https://patreon.com/example')).toBeDefined();
    expect(screen.getByDisplayValue('Jane Doe')).toBeDefined();
    expect(screen.getByDisplayValue('host')).toBeDefined();
  });

  it('adds a funding row and sends it (plus the scalar p20 keys) in the PATCH payload', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add funding' }));
    await user.type(screen.getByLabelText('Label'), 'Support us on Patreon');
    await user.type(screen.getByLabelText('URL'), 'https://patreon.com/example');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.p20.funding).toEqual([
      { text: 'Support us on Patreon', url: 'https://patreon.com/example' },
    ]);
    // The scalar keys ride in the same p20 object as the tag arrays --
    // extending the payload with the five arrays must not drop them.
    expect(payload.p20.medium).toBe('podcast');
    expect(payload.p20.locked).toBe('yes');
  });

  it('sends [] for a tag whose last row was removed, clearing it', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({
      p20: {
        medium: 'podcast',
        locked: 'yes',
        locked_owner: '',
        license: [{ text: 'CC BY 4.0', url: 'https://creativecommons.org/licenses/by/4.0/' }],
      },
    }));

    await user.click(screen.getByRole('button', { name: 'Remove License row 1' }));
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.p20.license).toEqual([]);
  });

  it('blocks save with an inline message when a funding row has no url', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add funding' }));
    await user.type(screen.getByLabelText('Label'), 'Support us');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Every funding row needs a URL.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('seeds a podroll row from feed.p20 and sends it in the PATCH payload', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({
      p20: {
        medium: 'podcast',
        locked: 'yes',
        locked_owner: '',
        podroll: [{ feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9', feedUrl: 'https://example.com/feed.xml' }],
      },
    }));

    expect(screen.getByDisplayValue('29cdca4a-32d8-56ba-b48b-09a011c5daa9')).toBeDefined();
    expect(screen.getByDisplayValue('https://example.com/feed.xml')).toBeDefined();

    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.p20.podroll).toEqual([
      { feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9', feedUrl: 'https://example.com/feed.xml' },
    ]);
  });

  it('blocks save with an inline message when a podroll row has no feed GUID', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add show' }));
    await user.type(screen.getByLabelText('Feed URL'), 'https://example.com/feed.xml');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Every podroll row needs a feed GUID.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('sends [] for podroll when its last row is removed, clearing it', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({
      p20: {
        medium: 'podcast',
        locked: 'yes',
        locked_owner: '',
        podroll: [{ feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9' }],
      },
    }));

    await user.click(screen.getByRole('button', { name: 'Remove Podroll row 1' }));
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.p20.podroll).toEqual([]);
  });

  it('forces the "Podcasting 2.0 tags" section open when a validation error blocks save, with the error itself outside the section', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    const sectionDiv = () => screen.getByRole('heading', { name: 'Podcasting 2.0 tags' }).closest('div')!;
    expect(sectionDiv().getAttribute('data-force-open')).toBe('false');

    await user.click(screen.getByRole('button', { name: '+ Add show' }));
    await user.type(screen.getByLabelText('Feed URL'), 'https://example.com/feed.xml');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    const error = await screen.findByText('Every podroll row needs a feed GUID.');
    expect(sectionDiv().getAttribute('data-force-open')).toBe('true');
    expect(sectionDiv().contains(error)).toBe(false);
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('renders podroll medium as a select and includes itemGuid in the saved payload', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({
      p20: {
        medium: 'podcast',
        locked: 'yes',
        locked_owner: '',
        podroll: [{ feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9', itemGuid: 'item-guid-1', medium: 'music' }],
      },
    }));

    // Distinguishes the row's medium <select> from the channel-level one
    // (also labeled "Medium") by its selected value, which only the
    // podroll row has.
    expect(screen.getByDisplayValue('music').tagName).toBe('SELECT');

    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.p20.podroll).toEqual([
      { feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9', itemGuid: 'item-guid-1', medium: 'music' },
    ]);
  });

  it('sends a changed podroll medium selection in the saved payload', async () => {
    const user = userEvent.setup();
    mockUpdateFeed.mockResolvedValue(makeFeed());
    renderPanel(makeFeed({
      p20: {
        medium: 'podcast',
        locked: 'yes',
        locked_owner: '',
        podroll: [{ feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9' }],
      },
    }));

    // [0] is the channel-level medium select; the podroll row's is the last.
    const mediumSelects = screen.getAllByLabelText('Medium');
    await user.selectOptions(mediumSelects[mediumSelects.length - 1], 'audiobook');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalled());
    const payload = mockUpdateFeed.mock.calls[0][1];
    expect(payload.p20.podroll).toEqual([
      { feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9', medium: 'audiobook' },
    ]);
  });

  it('blocks save with an inline message when a podroll feedGuid is not a valid UUID', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add show' }));
    await user.type(screen.getByLabelText('Feed GUID'), 'not-a-uuid');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Every podroll feed GUID must be a valid UUID.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('blocks save with an inline message when podroll has more than 50 rows', async () => {
    const user = userEvent.setup();
    const podroll = Array.from({ length: 51 }, () => ({ feedGuid: '29cdca4a-32d8-56ba-b48b-09a011c5daa9' }));
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '', podroll } }));

    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Podroll can have at most 50 shows.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('blocks save with an inline message when a license row has no name', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add license' }));
    await user.type(screen.getByLabelText('URL'), 'https://creativecommons.org/licenses/by/4.0/');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Every license row needs a name.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('blocks save with an inline message when a location row has no name', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add location' }));
    await user.type(screen.getByLabelText('Geo'), 'geo:45.5,-122.6');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Every location row needs a name.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('blocks save with an inline message when a text row has no value', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ p20: { medium: 'podcast', locked: 'yes', locked_owner: '' } }));

    await user.click(screen.getByRole('button', { name: '+ Add text' }));
    await user.type(screen.getByLabelText('Purpose'), 'verify');
    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    expect(await screen.findByText('Every text row needs a value.')).toBeDefined();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  // ---- Bulk upload progress (sequential per-file uploads) ----

  it('uploads files one at a time with advancing "x of y" progress, then scans once', async () => {
    const user = userEvent.setup();
    mockImportUpload
      .mockResolvedValueOnce({ staged: ['a.mp3'], rejected: [] })
      .mockResolvedValueOnce({ staged: ['b.mp3'], rejected: [] })
      .mockResolvedValueOnce({ staged: ['c.mp3'], rejected: [] });
    // Held open so the "3 of 3" progress line is observable before the
    // trailing scan resolves and clears it.
    let resolveScan: (plan: ImportPlan) => void = () => {};
    mockImportScan.mockReturnValue(new Promise((resolve) => { resolveScan = resolve; }));
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    const files = [
      new File(['a'], 'a.mp3', { type: 'audio/mpeg' }),
      new File(['b'], 'b.mp3', { type: 'audio/mpeg' }),
      new File(['c'], 'c.mp3', { type: 'audio/mpeg' }),
    ];
    await user.upload(fileInput, files);

    // One importUpload call per file, not one call for the whole batch.
    await waitFor(() => expect(mockImportUpload).toHaveBeenCalledTimes(3));
    expect(mockImportUpload).toHaveBeenNthCalledWith(1, 'archive-show', [files[0]]);
    expect(mockImportUpload).toHaveBeenNthCalledWith(2, 'archive-show', [files[1]]);
    expect(mockImportUpload).toHaveBeenNthCalledWith(3, 'archive-show', [files[2]]);

    await waitFor(() => expect(screen.getByText('Uploading 3 of 3...')).toBeDefined());

    resolveScan(makePlan());
    await waitFor(() => expect(screen.queryByText(/Uploading \d+ of \d+/)).toBeNull());
    expect(mockImportScan).toHaveBeenCalledTimes(1);
    expect(mockImportScan).toHaveBeenCalledWith('archive-show', { source: 'staging', overwrite: false });
  });

  it('continues the batch when one file fails to upload, landing it in rejected', async () => {
    const user = userEvent.setup();
    mockImportUpload
      .mockResolvedValueOnce({ staged: ['a.mp3'], rejected: [] })
      .mockRejectedValueOnce(new Error('network blip'))
      .mockResolvedValueOnce({ staged: ['c.mp3'], rejected: [] });
    mockImportScan.mockResolvedValue(makePlan());
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    const files = [
      new File(['a'], 'a.mp3', { type: 'audio/mpeg' }),
      new File(['b'], 'b.mp3', { type: 'audio/mpeg' }),
      new File(['c'], 'c.mp3', { type: 'audio/mpeg' }),
    ];
    await user.upload(fileInput, files);

    // The failed file didn't stop the third file from uploading, and the
    // batch still reached the scan step.
    await waitFor(() => expect(mockImportUpload).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(mockImportScan).toHaveBeenCalledTimes(1));
    expect(screen.getByText('b.mp3')).toBeDefined();
    expect(screen.getByText('network blip')).toBeDefined();
  });

  it('keeps the per-file rejected list visible when the trailing scan itself fails', async () => {
    const user = userEvent.setup();
    // One real per-file rejection during upload, then the scan step (which
    // runs once after the whole batch uploads) throws.
    mockImportUpload
      .mockResolvedValueOnce({ staged: [], rejected: [{ file: 'bad.flac', reason: 'not a supported audio format (only .mp3 is imported)' }] })
      .mockResolvedValueOnce({ staged: ['a.mp3'], rejected: [] });
    mockImportScan.mockRejectedValue(new Error('scan blew up'));
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    const files = [
      new File(['x'], 'bad.flac', { type: 'audio/x-flac' }),
      new File(['a'], 'a.mp3', { type: 'audio/mpeg' }),
    ];
    await user.upload(fileInput, files);

    await waitFor(() => expect(screen.getByText(/scan blew up/)).toBeDefined());
    // The upload step's own rejection must still be visible even though
    // the scan that followed it failed -- it was committed to state
    // before the scan call, not only inside the mutation's onSuccess.
    expect(screen.getByText('bad.flac')).toBeDefined();
    expect(screen.getByText(/not a supported audio format/)).toBeDefined();
  });

  // ---- Add-episode / bulk-import affordances after a completed import ----

  it('keeps Add episode, Choose files, and Scan server directory enabled once the import status is done', async () => {
    mockImportStatus.mockResolvedValue({
      state: 'done',
      processed: 1,
      total: 1,
      startedAt: '2026-01-01T00:00:00Z',
      report: { committed: [{ episodeId: 's01e01' }], skipped: [], failed: [], queued: [] },
    });
    renderPanel(makeFeed());

    await waitFor(() => expect(screen.getByText('Import complete')).toBeDefined());

    expect((screen.getByRole('button', { name: 'Add episode' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: 'Choose files' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: 'Scan server directory' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('dismisses the completed-import report via Clear report', async () => {
    const user = userEvent.setup();
    mockImportStatus.mockResolvedValue({
      state: 'done',
      processed: 1,
      total: 1,
      startedAt: '2026-01-01T00:00:00Z',
      report: { committed: [{ episodeId: 's01e01' }], skipped: [], failed: [], queued: [] },
    });
    renderPanel(makeFeed());

    await waitFor(() => expect(screen.getByText('Import complete')).toBeDefined());
    await user.click(screen.getByRole('button', { name: 'Clear report' }));

    expect(screen.queryByText('Import complete')).toBeNull();
  });

  it('dismisses a failed-import report via Clear report', async () => {
    const user = userEvent.setup();
    mockImportStatus.mockResolvedValue({
      state: 'error',
      processed: 0,
      total: 1,
      startedAt: '2026-01-01T00:00:00Z',
      report: { committed: [], skipped: [], failed: [], queued: [], error: 'disk full' },
    });
    renderPanel(makeFeed());

    await waitFor(() => expect(screen.getByText(/Import failed: disk full/)).toBeDefined());
    await user.click(screen.getByRole('button', { name: 'Clear report' }));

    expect(screen.queryByText(/Import failed/)).toBeNull();
  });

  // ---- Overwrite toggle ----

  it('threads the checked overwrite state into both the scan call and the commit call', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    const plan = makePlan({ overwrite: true });
    plan.entries[0] = { ...plan.entries[0], replacesExisting: true };
    mockImportScan.mockResolvedValue(plan);
    mockImportCommit.mockResolvedValue({ message: 'import started' });
    renderPanel(makeFeed());

    await user.click(screen.getByLabelText('Replace episodes that already exist'));

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));

    await waitFor(() => expect(mockImportScan).toHaveBeenCalledWith('archive-show', { source: 'staging', overwrite: true }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    // The scanned entry actually collides (replacesExisting) and cleanly
    // committed (no errors, since overwrite was on), so the "replace"
    // clause counts it and the preview table flags the row.
    // Renders in both the desktop table row and the mobile card (jsdom has
    // no CSS to actually hide either), same duplication the file's other
    // table-scoped assertions already account for.
    expect(screen.getAllByText('replaces').length).toBeGreaterThanOrEqual(1);
    const confirmButton = screen.getByRole('button', { name: 'Import 1 episode and replace 1 existing' });
    await user.click(confirmButton);

    await waitFor(() => expect(mockImportCommit).toHaveBeenCalledWith('archive-show', {
      planHash: 'hash-abc123',
      source: 'staging',
      overwrite: true,
    }));
  });

  it('hides the "replace" clause when overwrite is on but nothing in the plan actually collides', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    // overwrite=true but replacesExisting stays false on the only entry --
    // an overwrite-enabled scan that happens to have no collisions must
    // not claim it's replacing anything.
    mockImportScan.mockResolvedValue(makePlan({ overwrite: true }));
    renderPanel(makeFeed());

    await user.click(screen.getByLabelText('Replace episodes that already exist'));
    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    expect(screen.getByRole('button', { name: 'Import 1 episode' })).toBeDefined();
    expect(screen.queryByText('replaces')).toBeNull();
  });

  it('threads overwrite into the directory scan call too', async () => {
    const user = userEvent.setup();
    mockImportScan.mockResolvedValue(makePlan({ overwrite: true }));
    renderPanel(makeFeed());

    await user.click(screen.getByLabelText('Replace episodes that already exist'));
    await user.click(screen.getByRole('button', { name: 'Scan server directory' }));

    await waitFor(() => expect(mockImportScan).toHaveBeenCalledWith('archive-show', { source: 'directory', overwrite: true }));
  });

  it('keeps commit on the scanned overwrite value even if the checkbox is toggled off afterward', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    mockImportScan.mockResolvedValue(makePlan({ overwrite: true }));
    mockImportCommit.mockResolvedValue({ message: 'import started' });
    renderPanel(makeFeed());

    await user.click(screen.getByLabelText('Replace episodes that already exist'));
    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    // Toggling the checkbox back off after the scan must not change what
    // commit sends -- it always replays plan.overwrite (the value the
    // shown plan, and its planHash, were actually built with), not the
    // live checkbox, so scan and commit can never desync and 409.
    await user.click(screen.getByLabelText('Replace episodes that already exist'));
    await user.click(screen.getByRole('button', { name: /^Import 1 episode/ }));

    await waitFor(() => expect(mockImportCommit).toHaveBeenCalledWith('archive-show', {
      planHash: 'hash-abc123',
      source: 'staging',
      overwrite: true,
    }));
  });

  // ---- Staging lifecycle (cancel clears staging; leftovers surfaced) ----

  it('clears staging when the plan is canceled', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    mockImportScan.mockResolvedValue(makePlan());
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(mockClearImportStaging).toHaveBeenCalledWith('archive-show'));
    expect(document.querySelector('table')).toBeNull();
  });

  it('shows a note and a clear-staged-files button when the scanned plan has more entries than the batch just uploaded', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    // Two entries came back from the scan even though only one file was
    // just uploaded -- the second one is left over from an earlier,
    // canceled attempt that staged it and never committed.
    const twoEntryPlan = makePlan({
      entries: [
        { ...makePlan().entries[0], episodeId: 's01e01', audioFile: 'a.mp3' },
        { ...makePlan().entries[0], episodeId: 's01e02', audioFile: 'b.mp3' },
      ],
    });
    mockImportScan.mockResolvedValue(twoEntryPlan);
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    expect(screen.getByText(/left over from an earlier attempt/)).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Clear staged files' }));

    await waitFor(() => expect(mockClearImportStaging).toHaveBeenCalledWith('archive-show'));
    expect(document.querySelector('table')).toBeNull();
  });

  it('does not show the leftover-files note when the scan matches exactly what was just uploaded', async () => {
    const user = userEvent.setup();
    mockImportUpload.mockResolvedValue({ staged: ['a.mp3'], rejected: [] });
    mockImportScan.mockResolvedValue(makePlan());
    renderPanel(makeFeed());

    const fileInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement;
    await user.upload(fileInput, new File(['x'], 'a.mp3', { type: 'audio/mpeg' }));
    await waitFor(() => expect(document.querySelector('table')).not.toBeNull());

    expect(screen.queryByText(/left over from an earlier attempt/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Clear staged files' })).toBeNull();
  });

  // ---- Auto-refresh after import completes ----

  it('invalidates the episodes and feed queries once the import status flips from running to done', async () => {
    mockImportStatus.mockResolvedValue({
      state: 'running', processed: 0, total: 1, startedAt: '2026-01-01T00:00:00Z',
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
    render(
      <QueryClientProvider client={client}>
        <LocalFeedPanel feed={makeFeed()} slug="archive-show" />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Importing 0 \/ 1/)).toBeDefined());
    expect(invalidateSpy).not.toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['episodes', 'archive-show'] }));

    // Push the status query straight to 'done' the way the real refetch
    // would once the background commit finishes -- this is what the
    // component's useEffect watches for.
    client.setQueryData(['import-status', 'archive-show'], {
      state: 'done',
      processed: 1,
      total: 1,
      startedAt: '2026-01-01T00:00:00Z',
      report: { committed: [{ episodeId: 's01e01' }], skipped: [], failed: [], queued: [] },
    });

    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['episodes', 'archive-show'] }));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['feed', 'archive-show'] });
  });
});
