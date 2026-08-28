import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection, { useCollapsibleOpen } from '../../components/CollapsibleSection';
import { Modal } from '../../components/Modal';
import Checkbox from '../../components/Checkbox';
import LoadingSpinner from '../../components/LoadingSpinner';
import ImportPreviewTable from '../../components/ImportPreviewTable';
import { getErrorMessage } from '../../api/client';
import {
  updateFeed, uploadFeedArtwork, uploadLocalEpisode,
  importUpload, importScan, importCommit, importStatus,
} from '../../api/feeds';
import type { UpdateFeedPayload, ImportPlan, ImportSource, ImportRejectedFile } from '../../api/feeds';
import type { Feed } from '../../api/types';
import { btnPrimary, btnSecondary, btnOutline } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { fromDatetimeLocalInput } from '../../utils/format';

// Podcasting 2.0 channel-level scalar tags (design spec section 6, mirrored
// from src/api/feeds.py's _P20_MEDIUM_VALUES / _P20_LOCKED_VALUES).
const P20_MEDIUM_OPTIONS = ['podcast', 'music', 'video', 'film', 'audiobook', 'newsletter', 'blog'] as const;

// Mirrors api/feeds.py's _P20_FEED_GUID_RE exactly (standard 8-4-4-4-12 hex
// UUID, case insensitive) so a malformed podroll feedGuid is caught here
// instead of round-tripping to the API for a 400.
const P20_FEED_GUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// Mirrors api/feeds.py's _P20_PODROLL_MAX.
const P20_PODROLL_MAX_ROWS = 50;

const fieldCls = 'w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring';
const fileInputCls = `block w-full text-sm text-muted-foreground file:mr-3 file:px-3 file:py-1.5 file:rounded file:border-0 file:text-sm ${btnSecondary} file:transition-colors ${focusRing}`;

// ---- Podcasting 2.0 list tags (funding/person/license/location/txt/podroll) ----
// One row shape (plain string fields) serves all six tags; the field list
// per tag mirrors local_feed_builder.py's attribute whitelist (_FUNDING_ATTRS
// etc.) and api/feeds.py's _validate_p20_items / _validate_p20_podroll, so
// the UI can never offer an attribute the backend would strip. podroll rows
// have no 'text' field -- the framework treats every field uniformly, so
// that needs no special casing here.
type P20Row = Record<string, string>;

interface P20FieldDef {
  key: string;
  label: string;
  placeholder: string;
  // When set, the field renders as a <select> over these values (plus a
  // blank "not set" option) instead of a free-text input -- e.g. podroll's
  // per-row medium, kept in parity with the channel-level medium select.
  options?: readonly string[];
}

interface P20TagDef {
  tag: 'funding' | 'person' | 'license' | 'location' | 'txt' | 'podroll';
  title: string;
  hint: string;
  addLabel: string;
  fields: P20FieldDef[];
  // Client-side mirror of the backend's hard requirements
  // (_validate_p20_items: funding needs a url, person needs a name/text;
  // _validate_p20_podroll: every entry needs a feedGuid) so a bad row is
  // caught before the request instead of surfacing a 400.
  requiredKey?: string;
  requiredError?: string;
  // Extra shape check beyond presence -- only podroll's feedGuid needs one
  // (must look like a UUID). Only checked when the field has a value, so it
  // never duplicates a requiredKey miss on the same row.
  patternKey?: string;
  pattern?: RegExp;
  patternError?: string;
  // Caps the row count client-side, mirroring the backend's own cap
  // (_validate_p20_podroll's 50-entry limit) so it surfaces here instead of
  // as a 400 after the request round-trips.
  maxRows?: number;
  maxRowsError?: string;
}

const P20_TAG_DEFS: P20TagDef[] = [
  {
    tag: 'funding',
    title: 'Funding',
    hint: 'Links listeners can use to support the show, like Patreon or Ko-fi.',
    addLabel: '+ Add funding',
    fields: [
      { key: 'text', label: 'Label', placeholder: 'Support us on Patreon' },
      { key: 'url', label: 'URL', placeholder: 'https://...' },
    ],
    requiredKey: 'url',
    requiredError: 'Every funding row needs a URL.',
  },
  {
    tag: 'person',
    title: 'People',
    hint: 'Hosts, guests, and other people to credit.',
    addLabel: '+ Add person',
    fields: [
      { key: 'text', label: 'Name', placeholder: 'Jane Doe' },
      { key: 'role', label: 'Role', placeholder: 'host' },
      { key: 'group', label: 'Group', placeholder: 'cast' },
      { key: 'img', label: 'Photo URL', placeholder: 'https://...' },
      { key: 'href', label: 'Link URL', placeholder: 'https://...' },
    ],
    requiredKey: 'text',
    requiredError: 'Every person row needs a name.',
  },
  {
    tag: 'license',
    title: 'License',
    hint: 'The license this show is released under.',
    addLabel: '+ Add license',
    fields: [
      { key: 'text', label: 'License name', placeholder: 'CC BY-NC-ND 4.0' },
      { key: 'url', label: 'URL', placeholder: 'https://...' },
    ],
    requiredKey: 'text',
    requiredError: 'Every license row needs a name.',
  },
  {
    tag: 'location',
    title: 'Location',
    hint: 'Where the show is set or recorded.',
    addLabel: '+ Add location',
    fields: [
      { key: 'text', label: 'Name', placeholder: 'Portland, OR' },
      { key: 'geo', label: 'Geo', placeholder: 'geo:45.5,-122.6' },
      { key: 'osm', label: 'OpenStreetMap ID', placeholder: 'R123456' },
    ],
    requiredKey: 'text',
    requiredError: 'Every location row needs a name.',
  },
  {
    tag: 'txt',
    title: 'Text',
    hint: "Freeform values for anything the other tags don't cover.",
    addLabel: '+ Add text',
    fields: [
      { key: 'text', label: 'Text', placeholder: 'Free text' },
      { key: 'purpose', label: 'Purpose', placeholder: 'verify' },
    ],
    requiredKey: 'text',
    requiredError: 'Every text row needs a value.',
  },
  {
    tag: 'podroll',
    title: 'Podroll',
    hint: 'Other shows to recommend alongside this one.',
    addLabel: '+ Add show',
    fields: [
      { key: 'feedGuid', label: 'Feed GUID', placeholder: '29cdca4a-32d8-56ba-b48b-09a011c5daa9' },
      { key: 'feedUrl', label: 'Feed URL', placeholder: 'https://...' },
      { key: 'itemGuid', label: 'Item GUID', placeholder: 'Optional' },
      { key: 'medium', label: 'Medium', placeholder: 'podcast', options: P20_MEDIUM_OPTIONS },
    ],
    requiredKey: 'feedGuid',
    requiredError: 'Every podroll row needs a feed GUID.',
    patternKey: 'feedGuid',
    pattern: P20_FEED_GUID_RE,
    patternError: 'Every podroll feed GUID must be a valid UUID.',
    maxRows: P20_PODROLL_MAX_ROWS,
    maxRowsError: `Podroll can have at most ${P20_PODROLL_MAX_ROWS} shows.`,
  },
];

// Reads whatever feed.p20[tag] holds (loosely typed on the wire) into the
// row shape the editors use. Non-string values are dropped rather than
// coerced, since a stray non-string attr couldn't have come from this form.
function normalizeP20Rows(raw: unknown): P20Row[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => {
    const row: P20Row = {};
    if (item && typeof item === 'object') {
      for (const [key, value] of Object.entries(item as Record<string, unknown>)) {
        if (typeof value === 'string') row[key] = value;
      }
    }
    return row;
  });
}

// Trims every field and drops empty ones, then drops any row left with no
// fields at all -- a row the user added and never filled in is treated as
// nothing rather than an empty object sent to the API. Also how a tag's
// rows collapse to [] when every row is removed (send [], never undefined,
// so the PATCH actually clears the tag).
function cleanP20Rows(rows: P20Row[]): P20Row[] {
  return rows
    .map((row) => {
      const cleaned: P20Row = {};
      for (const [key, value] of Object.entries(row)) {
        const trimmed = value.trim();
        if (trimmed) cleaned[key] = trimmed;
      }
      return cleaned;
    })
    .filter((row) => Object.keys(row).length > 0);
}

interface Props {
  feed: Feed;
  slug: string;
}

function LocalFeedPanel({ feed, slug }: Props) {
  const queryClient = useQueryClient();
  // Open by default: for a local feed this panel is the primary management
  // UI (there's no source RSS to fall back on), unlike the drill-down panels
  // below it that default closed.
  const [panelOpen, setPanelOpen] = useCollapsibleOpen(`local-feed-${slug}`, true);

  // ---- Metadata edit ----
  const [title, setTitle] = useState(feed.title);
  const [description, setDescription] = useState(feed.description ?? '');
  const [author, setAuthor] = useState(feed.author ?? '');
  const [explicit, setExplicit] = useState(feed.explicit ?? false);
  const [categoriesInput, setCategoriesInput] = useState((feed.categories ?? []).join(', '));
  const [medium, setMedium] = useState((feed.p20?.medium as string) ?? 'podcast');
  // 'yes' fallback: a local feed's p20.locked is always set at creation
  // (see api/feeds.py's add_local_feed), so an empty value here only ever
  // happens on a backend that predates local feeds.
  const [locked, setLocked] = useState((feed.p20?.locked as string) ?? 'yes');
  const [lockedOwner, setLockedOwner] = useState((feed.p20?.locked_owner as string) ?? '');
  const [fundingRows, setFundingRows] = useState<P20Row[]>(() => normalizeP20Rows(feed.p20?.funding));
  const [personRows, setPersonRows] = useState<P20Row[]>(() => normalizeP20Rows(feed.p20?.person));
  const [licenseRows, setLicenseRows] = useState<P20Row[]>(() => normalizeP20Rows(feed.p20?.license));
  const [locationRows, setLocationRows] = useState<P20Row[]>(() => normalizeP20Rows(feed.p20?.location));
  const [txtRows, setTxtRows] = useState<P20Row[]>(() => normalizeP20Rows(feed.p20?.txt));
  const [podrollRows, setPodrollRows] = useState<P20Row[]>(() => normalizeP20Rows(feed.p20?.podroll));
  const [p20ValidationError, setP20ValidationError] = useState<string | null>(null);
  const [metaSaved, setMetaSaved] = useState(false);

  // Rows/setters per tag, keyed the same way P20_TAG_DEFS is, so the editor
  // markup below and the save handler can both loop over P20_TAG_DEFS
  // instead of six near-identical blocks.
  const p20RowsByTag: Record<string, P20Row[]> = {
    funding: fundingRows, person: personRows, license: licenseRows, location: locationRows, txt: txtRows,
    podroll: podrollRows,
  };
  const p20SettersByTag: Record<string, Dispatch<SetStateAction<P20Row[]>>> = {
    funding: setFundingRows, person: setPersonRows, license: setLicenseRows, location: setLocationRows, txt: setTxtRows,
    podroll: setPodrollRows,
  };

  // Reseed the form from the server feed object whenever its identity
  // changes (a successful save or a background refetch), same idiom
  // FeedSettingsPanel uses for its per-field inputs.
  useSyncFromQuery(feed, (f) => {
    setTitle(f.title);
    setDescription(f.description ?? '');
    setAuthor(f.author ?? '');
    setExplicit(f.explicit ?? false);
    setCategoriesInput((f.categories ?? []).join(', '));
    setMedium((f.p20?.medium as string) ?? 'podcast');
    setLocked((f.p20?.locked as string) ?? 'yes');
    setLockedOwner((f.p20?.locked_owner as string) ?? '');
    setFundingRows(normalizeP20Rows(f.p20?.funding));
    setPersonRows(normalizeP20Rows(f.p20?.person));
    setLicenseRows(normalizeP20Rows(f.p20?.license));
    setLocationRows(normalizeP20Rows(f.p20?.location));
    setTxtRows(normalizeP20Rows(f.p20?.txt));
    setPodrollRows(normalizeP20Rows(f.p20?.podroll));
  });

  const metaMutation = useMutation({
    mutationFn: (data: UpdateFeedPayload) => updateFeed(slug, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      setMetaSaved(true);
      setTimeout(() => setMetaSaved(false), 2000);
    },
  });

  const artworkMutation = useMutation({
    mutationFn: (file: File) => uploadFeedArtwork(slug, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
  });

  const handleSaveMetadata = (e: React.FormEvent) => {
    e.preventDefault();
    const categories = categoriesInput.split(',').map((c) => c.trim()).filter(Boolean);

    // Clean every tag's rows and check each def's client-side requirements
    // (presence, shape, row count) before this ever reaches the API --
    // mirrors api/feeds.py's _validate_p20_items / _validate_p20_podroll so
    // a bad row is caught here instead of surfacing as a 400.
    const cleanedTags: Record<string, P20Row[]> = {};
    const tagErrors: string[] = [];
    for (const def of P20_TAG_DEFS) {
      const rows = cleanP20Rows(p20RowsByTag[def.tag]);
      cleanedTags[def.tag] = rows;
      if (def.requiredKey && rows.some((row) => !row[def.requiredKey!])) {
        tagErrors.push(def.requiredError!);
      }
      if (def.patternKey && def.pattern
        && rows.some((row) => row[def.patternKey!] && !def.pattern!.test(row[def.patternKey!]))) {
        tagErrors.push(def.patternError!);
      }
      if (def.maxRows && rows.length > def.maxRows) {
        tagErrors.push(def.maxRowsError!);
      }
    }
    if (tagErrors.length > 0) {
      setP20ValidationError(tagErrors.join(' '));
      return;
    }
    setP20ValidationError(null);

    // locked_owner always sent, even blank: '' is the backend's delete
    // sentinel for clearing a previously-set owner (_validate_p20_scalar),
    // so omitting the key here would leave a cleared owner un-cleared. The
    // six tag arrays are likewise always sent -- an empty array is how a
    // cleared tag reaches the backend (undefined would drop the key and
    // leave the old rows untouched).
    metaMutation.mutate({
      title: title.trim(),
      description: description.trim(),
      // null (not undefined) so a blanked field actually clears server-side
      // -- undefined drops the key from the JSON body, and the backend only
      // clears a field it sees explicitly set to null.
      author: author.trim() || null,
      explicit,
      categories: categories.length ? categories : null,
      p20: { medium, locked, locked_owner: lockedOwner.trim(), ...cleanedTags },
    });
  };

  // ---- Add episode ----
  const [addEpisodeOpen, setAddEpisodeOpen] = useState(false);

  // ---- Bulk import ----
  const importFileInputRef = useRef<HTMLInputElement>(null);
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [importSource, setImportSource] = useState<ImportSource>('staging');
  const [uploadRejected, setUploadRejected] = useState<ImportRejectedFile[]>([]);
  const [importError, setImportError] = useState<string | null>(null);

  const uploadAndScanMutation = useMutation({
    mutationFn: async (files: File[]) => {
      const uploaded = await importUpload(slug, files);
      const scanned = await importScan(slug, { source: 'staging' });
      return { uploaded, scanned };
    },
    // Clear a stale error from a previous failed attempt as soon as a new
    // one starts, rather than leaving it displayed under the new state.
    onMutate: () => setImportError(null),
    onSuccess: ({ uploaded, scanned }) => {
      setUploadRejected(uploaded.rejected);
      setPlan(scanned);
      setImportSource('staging');
    },
    onError: (e) => setImportError(getErrorMessage(e, 'Upload failed')),
  });

  const scanDirectoryMutation = useMutation({
    mutationFn: () => importScan(slug, { source: 'directory' }),
    onMutate: () => setImportError(null),
    onSuccess: (scanned) => {
      setUploadRejected([]);
      setPlan(scanned);
      setImportSource('directory');
    },
    onError: (e) => setImportError(getErrorMessage(e, 'Scan failed')),
  });

  const commitMutation = useMutation({
    mutationFn: () => importCommit(slug, { planHash: plan!.planHash, source: importSource }),
    onSuccess: () => {
      setPlan(null);
      queryClient.invalidateQueries({ queryKey: ['import-status', slug] });
    },
    onError: (e) => setImportError(getErrorMessage(e, 'Import failed')),
  });

  const handleImportFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = '';
    if (!files.length) return;
    uploadAndScanMutation.mutate(files);
  };

  const statusQuery = useQuery({
    queryKey: ['import-status', slug],
    queryFn: () => importStatus(slug),
    enabled: panelOpen,
    refetchInterval: (query) => (query.state.data?.state === 'running' ? 2000 : false),
  });
  const importState = statusQuery.data?.state;
  const importRunning = importState === 'running';

  // A batch that just finished changes the episode list and the feed's
  // episode count; refetch both once (not on every poll tick).
  useEffect(() => {
    if (importState === 'done') {
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    }
  }, [importState, slug, queryClient]);

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Local feed"
        subtitle="Manage this feed's episodes and artwork directly. There's no source RSS to refresh from."
        defaultOpen={true}
        storageKey={`local-feed-${slug}`}
        onToggle={setPanelOpen}
      >
        {feed.hasArtwork === false && (
          <div className="mb-4 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
            No artwork uploaded. Podcast apps may reject this feed.
          </div>
        )}

        <form onSubmit={handleSaveMetadata} className="space-y-4 mb-6">
          <h3 className="text-sm font-semibold text-foreground">Feed metadata</h3>
          <div>
            <label htmlFor={`local-title-${slug}`} className="block text-sm font-medium text-foreground mb-2">Title</label>
            <input
              id={`local-title-${slug}`}
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              className={fieldCls}
            />
          </div>
          <div>
            <label htmlFor={`local-description-${slug}`} className="block text-sm font-medium text-foreground mb-2">Description</label>
            <textarea
              id={`local-description-${slug}`}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className={fieldCls}
            />
          </div>
          <div>
            <label htmlFor={`local-author-${slug}`} className="block text-sm font-medium text-foreground mb-2">Author</label>
            <input
              id={`local-author-${slug}`}
              type="text"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              className={fieldCls}
            />
          </div>
          <Checkbox
            checked={explicit}
            onChange={setExplicit}
            label="Explicit content"
          />
          <div>
            <label htmlFor={`local-categories-${slug}`} className="block text-sm font-medium text-foreground mb-2">Categories</label>
            <input
              id={`local-categories-${slug}`}
              type="text"
              value={categoriesInput}
              onChange={(e) => setCategoriesInput(e.target.value)}
              placeholder="Technology, Business"
              className={fieldCls}
            />
            <p className="mt-1 text-sm text-muted-foreground">Comma-separated, e.g. Technology, Business</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label htmlFor={`local-medium-${slug}`} className="block text-sm font-medium text-foreground mb-2">Medium</label>
              <select
                id={`local-medium-${slug}`}
                value={medium}
                onChange={(e) => setMedium(e.target.value)}
                className={`w-full ${selectBase}`}
              >
                {P20_MEDIUM_OPTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor={`local-locked-${slug}`} className="block text-sm font-medium text-foreground mb-2">Locked</label>
              <select
                id={`local-locked-${slug}`}
                value={locked}
                onChange={(e) => setLocked(e.target.value)}
                className={`w-full ${selectBase}`}
              >
                <option value="no">No</option>
                <option value="yes">Yes</option>
              </select>
            </div>
            <div>
              <label htmlFor={`local-locked-owner-${slug}`} className="block text-sm font-medium text-foreground mb-2">Locked owner email</label>
              <input
                id={`local-locked-owner-${slug}`}
                type="email"
                value={lockedOwner}
                onChange={(e) => setLockedOwner(e.target.value)}
                placeholder="owner@example.com"
                className={fieldCls}
              />
            </div>
          </div>
          <div>
            <label htmlFor={`local-artwork-${slug}`} className="block text-sm font-medium text-foreground mb-2">Artwork</label>
            <input
              id={`local-artwork-${slug}`}
              type="file"
              accept="image/jpeg,image/png"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = '';
                if (file) artworkMutation.mutate(file);
              }}
              className={fileInputCls}
            />
            {artworkMutation.isPending && <p className="mt-1 text-sm text-muted-foreground">Uploading...</p>}
            {artworkMutation.isSuccess && <p className="mt-1 text-sm text-success">Artwork updated.</p>}
            {artworkMutation.isError && (
              <p className="mt-1 text-sm text-destructive">{getErrorMessage(artworkMutation.error, 'Artwork upload failed')}</p>
            )}
          </div>

          <CollapsibleSection
            title="Podcasting 2.0 tags"
            subtitle="Funding, people, license, location, text, and podroll tags for apps that support them."
            defaultOpen={false}
            storageKey={`local-p20-${slug}`}
            // Forces the section open when a bad row blocks save, so the
            // error below is never left unreadable behind a collapsed,
            // overflow-hidden section (a silent-looking no-op on Save).
            forceOpen={!!p20ValidationError}
          >
            <div className="divide-y divide-border">
              {P20_TAG_DEFS.map((def) => (
                <P20TagEditor
                  key={def.tag}
                  slug={slug}
                  def={def}
                  rows={p20RowsByTag[def.tag]}
                  onChange={p20SettersByTag[def.tag]}
                  disabled={metaMutation.isPending}
                />
              ))}
            </div>
          </CollapsibleSection>

          {/* Rendered outside the collapsible section (which defaults
              closed) so a validation error is always visible, regardless
              of the section's open state, right alongside the save error. */}
          {p20ValidationError && (
            <p className="text-sm text-destructive">{p20ValidationError}</p>
          )}
          {metaMutation.isError && (
            <p className="text-sm text-destructive">{getErrorMessage(metaMutation.error, 'Could not save')}</p>
          )}
          <button
            type="submit"
            disabled={metaMutation.isPending || !title.trim()}
            className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors ${focusRing}`}
          >
            {metaMutation.isPending ? 'Saving...' : metaSaved ? 'Saved' : 'Save metadata'}
          </button>
        </form>

        <div className="pt-4 border-t border-border mb-6">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="text-sm font-semibold text-foreground">Episodes</h3>
            <button
              type="button"
              onClick={() => setAddEpisodeOpen(true)}
              className={`px-3 py-1.5 text-sm rounded ${btnPrimary} ${focusRing}`}
            >
              Add episode
            </button>
          </div>
          <p className="text-sm text-muted-foreground">Upload one episode's audio, or import a batch below.</p>
        </div>

        <div className="pt-4 border-t border-border">
          <h3 className="text-sm font-semibold text-foreground mb-1">Bulk import</h3>
          <p className="text-sm text-muted-foreground mb-1">
            Upload mp3s named like S01E01.mp3, with optional matching .txt/.jpg/.json sidecar files.
          </p>
          <p className="text-sm text-muted-foreground mb-3">
            For archives already on the server, place files in{' '}
            <code className="text-xs">import/{slug}</code> inside the MinusPod data
            directory and use Scan server directory. MinusPod moves the audio in
            when you commit; sidecar files stay where you put them.
          </p>
          <div className="flex flex-wrap gap-2 mb-3">
            <input
              ref={importFileInputRef}
              type="file"
              multiple
              accept=".mp3,.txt,.jpg,.jpeg,.png,.json"
              onChange={handleImportFiles}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => importFileInputRef.current?.click()}
              disabled={uploadAndScanMutation.isPending || importRunning}
              className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 ${focusRing}`}
            >
              {uploadAndScanMutation.isPending ? 'Uploading...' : 'Choose files'}
            </button>
            <button
              type="button"
              onClick={() => scanDirectoryMutation.mutate()}
              disabled={scanDirectoryMutation.isPending || importRunning}
              title="Scan the server-side import directory instead of uploading"
              className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 ${focusRing}`}
            >
              {scanDirectoryMutation.isPending ? 'Scanning...' : 'Scan server directory'}
            </button>
          </div>

          {importError && <p className="text-sm text-destructive mb-3">{importError}</p>}

          {plan && (
            <div className="mb-4">
              <ImportPreviewTable
                entries={plan.entries}
                rejected={[...uploadRejected, ...plan.rejected]}
                totals={plan.totals}
              />
              <div className="flex gap-2 mt-3">
                <button
                  type="button"
                  onClick={() => commitMutation.mutate()}
                  disabled={commitMutation.isPending || plan.totals.importable === 0 || importRunning}
                  className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 ${focusRing}`}
                >
                  {commitMutation.isPending
                    ? 'Starting...'
                    : `Import ${plan.totals.importable} episode${plan.totals.importable === 1 ? '' : 's'}`}
                </button>
                <button
                  type="button"
                  onClick={() => setPlan(null)}
                  className={`px-4 py-2 rounded-lg ${btnSecondary} ${focusRing}`}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {statusQuery.data && statusQuery.data.state !== 'idle' && (
            <div className="mt-4 p-3 rounded-lg bg-secondary/50 border border-border">
              {importState === 'running' ? (
                <p className="text-sm text-muted-foreground flex items-center gap-2">
                  <LoadingSpinner size="sm" inline /> Importing {statusQuery.data.processed} / {statusQuery.data.total}...
                </p>
              ) : importState === 'error' ? (
                <p className="text-sm text-destructive">
                  Import failed: {statusQuery.data.report?.error ?? 'unknown error'}
                </p>
              ) : importState === 'done' && statusQuery.data.report ? (
                <div className="text-sm">
                  <p className="font-medium text-foreground mb-1">Import complete</p>
                  <p className="text-muted-foreground">
                    {statusQuery.data.report.committed.length} committed,{' '}
                    {statusQuery.data.report.queued.length} queued for processing,{' '}
                    {statusQuery.data.report.skipped.length} skipped,{' '}
                    {statusQuery.data.report.failed.length} failed.
                  </p>
                  {statusQuery.data.report.failed.length > 0 && (
                    <ul className="mt-2 space-y-1 text-destructive">
                      {statusQuery.data.report.failed.map((f) => (
                        <li key={f.episodeId}>{f.episodeId} ({f.audioFile}): {f.error}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </CollapsibleSection>

      {addEpisodeOpen && (
        <AddEpisodeModal slug={slug} onClose={() => setAddEpisodeOpen(false)} />
      )}
    </div>
  );
}

interface P20TagEditorProps {
  slug: string;
  def: P20TagDef;
  rows: P20Row[];
  onChange: Dispatch<SetStateAction<P20Row[]>>;
  disabled: boolean;
}

// One tag's block inside the "Podcasting 2.0 tags" nested collapsible:
// existing rows (one bordered card per row, a labeled input per field, a
// Remove button) plus an Add button. Shared across all six tags -- the
// field list is the only thing that differs between them (P20_TAG_DEFS).
function P20TagEditor({ slug, def, rows, onChange, disabled }: P20TagEditorProps) {
  const addRow = () => onChange((prev) => [...prev, {}]);
  const removeRow = (idx: number) => onChange((prev) => prev.filter((_, i) => i !== idx));
  const updateField = (idx: number, key: string, value: string) => {
    onChange((prev) => prev.map((row, i) => (i === idx ? { ...row, [key]: value } : row)));
  };

  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <h4 className="text-sm font-medium text-foreground">{def.title}</h4>
      <p className="text-xs text-muted-foreground mb-2">{def.hint}</p>
      {rows.length > 0 && (
        <div className="space-y-2 mb-2">
          {rows.map((row, idx) => (
            // Index key: rows have no natural identity until a field is
            // filled in, and this list only grows/shrinks from user clicks
            // right here, never reorders.
            <div key={idx} className="rounded-md border border-border p-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {def.fields.map((field) => {
                  const inputId = `local-p20-${slug}-${def.tag}-${idx}-${field.key}`;
                  return (
                    <div key={field.key}>
                      <label htmlFor={inputId} className="block text-xs text-muted-foreground mb-1">
                        {field.label}
                      </label>
                      {field.options ? (
                        <select
                          id={inputId}
                          value={row[field.key] ?? ''}
                          onChange={(e) => updateField(idx, field.key, e.target.value)}
                          disabled={disabled}
                          className={`w-full ${selectBase}`}
                        >
                          <option value="">Not set</option>
                          {field.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                        </select>
                      ) : (
                        <input
                          id={inputId}
                          type="text"
                          value={row[field.key] ?? ''}
                          onChange={(e) => updateField(idx, field.key, e.target.value)}
                          placeholder={field.placeholder}
                          disabled={disabled}
                          className={fieldCls}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
              <button
                type="button"
                onClick={() => removeRow(idx)}
                disabled={disabled}
                aria-label={`Remove ${def.title} row ${idx + 1}`}
                className={`mt-2 text-xs text-muted-foreground hover:text-destructive disabled:opacity-50 ${focusRing}`}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={addRow}
        disabled={disabled}
        className={`px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50 ${focusRing}`}
      >
        {def.addLabel}
      </button>
    </div>
  );
}

interface AddEpisodeModalProps {
  slug: string;
  onClose: () => void;
}

function AddEpisodeModal({ slug, onClose }: AddEpisodeModalProps) {
  const queryClient = useQueryClient();
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [season, setSeason] = useState('');
  const [episode, setEpisode] = useState('');
  const [publishedAt, setPublishedAt] = useState('');
  const [description, setDescription] = useState('');
  const [artworkFile, setArtworkFile] = useState<File | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append('audio', audioFile!);
      if (title.trim()) form.append('title', title.trim());
      if (season.trim()) form.append('season', season.trim());
      if (episode.trim()) form.append('episode', episode.trim());
      const iso = fromDatetimeLocalInput(publishedAt);
      if (iso) form.append('publishedAt', iso);
      if (description.trim()) form.append('description', description.trim());
      if (artworkFile) form.append('artwork', artworkFile);
      return uploadLocalEpisode(slug, form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      onClose();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (audioFile) mutation.mutate();
  };

  return (
    <Modal onClose={onClose} panelClassName="max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
      <h2 className="text-xl font-semibold text-foreground mb-4">Add episode</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="add-ep-audio" className="block text-sm font-medium text-foreground mb-2">Audio file (.mp3)</label>
          <input
            id="add-ep-audio"
            type="file"
            accept=".mp3,audio/mpeg"
            required
            onChange={(e) => setAudioFile(e.target.files?.[0] ?? null)}
            className={fileInputCls}
          />
        </div>
        <div>
          <label htmlFor="add-ep-title" className="block text-sm font-medium text-foreground mb-2">Title</label>
          <input id="add-ep-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} className={fieldCls} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="add-ep-season" className="block text-sm font-medium text-foreground mb-2">Season</label>
            <input
              id="add-ep-season"
              type="number"
              min={1}
              value={season}
              onChange={(e) => setSeason(e.target.value)}
              placeholder="1"
              className={fieldCls}
            />
          </div>
          <div>
            <label htmlFor="add-ep-episode" className="block text-sm font-medium text-foreground mb-2">Episode</label>
            <input
              id="add-ep-episode"
              type="number"
              min={1}
              value={episode}
              onChange={(e) => setEpisode(e.target.value)}
              placeholder="next available"
              className={fieldCls}
            />
          </div>
        </div>
        <div>
          <label htmlFor="add-ep-published" className="block text-sm font-medium text-foreground mb-2">Published</label>
          <input
            id="add-ep-published"
            type="datetime-local"
            value={publishedAt}
            onChange={(e) => setPublishedAt(e.target.value)}
            className={fieldCls}
          />
          <p className="mt-1 text-sm text-muted-foreground">Leave blank to use the upload time.</p>
        </div>
        <div>
          <label htmlFor="add-ep-description" className="block text-sm font-medium text-foreground mb-2">Description</label>
          <textarea
            id="add-ep-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className={fieldCls}
          />
        </div>
        <div>
          <label htmlFor="add-ep-artwork" className="block text-sm font-medium text-foreground mb-2">Artwork (optional)</label>
          <input
            id="add-ep-artwork"
            type="file"
            accept="image/jpeg,image/png"
            onChange={(e) => setArtworkFile(e.target.files?.[0] ?? null)}
            className={fileInputCls}
          />
        </div>
        {mutation.isError && (
          <p className="text-sm text-destructive">{getErrorMessage(mutation.error, 'Upload failed')}</p>
        )}
        <div className="flex gap-3 justify-end pt-2">
          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className={`px-4 py-2 rounded ${btnSecondary} disabled:opacity-50 ${focusRing}`}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending || !audioFile}
            className={`px-4 py-2 rounded ${btnPrimary} disabled:opacity-50 ${focusRing}`}
          >
            {mutation.isPending ? 'Uploading...' : 'Add episode'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default LocalFeedPanel;
