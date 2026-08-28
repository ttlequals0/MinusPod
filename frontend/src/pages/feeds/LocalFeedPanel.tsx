import { useEffect, useRef, useState } from 'react';
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
import { btnPrimary, btnSecondary } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { fromDatetimeLocalInput } from '../../utils/format';

// Podcasting 2.0 channel-level scalar tags (design spec section 6, mirrored
// from src/api/feeds.py's _P20_MEDIUM_VALUES / _P20_LOCKED_VALUES).
const P20_MEDIUM_OPTIONS = ['podcast', 'music', 'video', 'film', 'audiobook', 'newsletter', 'blog'] as const;

const fieldCls = 'w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring';
const fileInputCls = `block w-full text-sm text-muted-foreground file:mr-3 file:px-3 file:py-1.5 file:rounded file:border-0 file:text-sm ${btnSecondary} file:transition-colors ${focusRing}`;

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
  const [metaSaved, setMetaSaved] = useState(false);

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
    // locked_owner always sent, even blank: '' is the backend's delete
    // sentinel for clearing a previously-set owner (_validate_p20_scalar),
    // so omitting the key here would leave a cleared owner un-cleared.
    metaMutation.mutate({
      title: title.trim(),
      description: description.trim(),
      // null (not undefined) so a blanked field actually clears server-side
      // -- undefined drops the key from the JSON body, and the backend only
      // clears a field it sees explicitly set to null.
      author: author.trim() || null,
      explicit,
      categories: categories.length ? categories : null,
      p20: { medium, locked, locked_owner: lockedOwner.trim() },
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
