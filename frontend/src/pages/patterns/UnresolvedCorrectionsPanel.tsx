import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  assignUnresolvedCorrection,
  deleteUnresolvedCorrection,
  bulkUpdateUnresolvedCorrections,
  getUnresolvedCorrections,
  type UnresolvedCorrection,
} from '../../api/patterns';
import Checkbox from '../../components/Checkbox';
import { btnDestructive, btnOutline, btnPrimary } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';
import { ConfirmModal } from '../../components/Modal';

function formatBounds(bounds: { start: number; end: number } | null): string | null {
  if (!bounds) return null;
  return `${bounds.start.toFixed(1)} s to ${bounds.end.toFixed(1)} s`;
}

interface CorrectionRowProps {
  correction: UnresolvedCorrection;
  selected: boolean;
  onSelected: (checked: boolean) => void;
}

function CorrectionRow({ correction, selected, onSelected }: CorrectionRowProps) {
  const queryClient = useQueryClient();
  const [slug, setSlug] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const mutation = useMutation({
    mutationFn: () => assignUnresolvedCorrection(correction.id, slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['unresolved-corrections'] }),
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteUnresolvedCorrection(correction.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['unresolved-corrections'] }),
  });
  const selectedCandidate = correction.candidates.find((candidate) => candidate.slug === slug);

  return (
    <li className="rounded border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex gap-3">
          <Checkbox
            ariaLabel={`Select correction ${correction.id}`}
            checked={selected}
            onChange={onSelected}
            className="min-h-11 min-w-11 shrink-0 justify-center sm:min-h-0 sm:min-w-0"
          />
          <div>
            <p className="font-medium text-foreground">Correction #{correction.id}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {correction.episode_title || correction.episode_id}
            </p>
            {correction.podcast_title && (
              <p className="text-xs text-muted-foreground">
                Saved under {correction.podcast_title}
              </p>
            )}
            {formatBounds(correction.original_bounds) && (
              <p className="text-xs text-muted-foreground">
                Original segment: {formatBounds(correction.original_bounds)}
              </p>
            )}
          </div>
        </div>
        <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
          {correction.correction_type.replace(/_/g, ' ')}
        </span>
      </div>

      {correction.candidates.length > 0 ? (
        <fieldset className="mt-4 space-y-2">
          <legend className="text-sm font-medium text-foreground">Choose the feed</legend>
          {correction.candidates.map((candidate) => (
            <div
              key={candidate.slug}
              className={`flex flex-col gap-2 rounded border p-3 sm:flex-row sm:items-center sm:gap-3 ${
                slug === candidate.slug ? 'border-primary bg-primary/5' : 'border-border'
              }`}
            >
              <label className="flex min-w-0 flex-1 cursor-pointer gap-3">
                <input
                  type="radio"
                  name={`correction-${correction.id}`}
                  value={candidate.slug}
                  checked={slug === candidate.slug}
                  onChange={() => { setSlug(candidate.slug); setConfirmed(false); }}
                  className="mt-1"
                />
                <span className="min-w-0">
                  <span className={`block text-sm font-medium text-foreground ${candidate.episode_available ? 'truncate' : 'break-words'}`}>
                    {candidate.podcast_title || candidate.slug}
                  </span>
                  <span className={`block text-xs text-muted-foreground ${candidate.episode_available ? 'truncate' : 'break-words'}`}>
                    {candidate.slug} / {candidate.episode_title || correction.episode_id}
                  </span>
                </span>
              </label>
              {candidate.episode_available ? (
                <a
                  href={`/ui/feeds/${encodeURIComponent(candidate.slug)}/episodes/${encodeURIComponent(correction.episode_id)}`}
                  className={`self-end text-xs text-primary hover:underline sm:shrink-0 sm:self-center ${focusRing}`}
                >
                  Review episode
                </a>
              ) : (
                <span className="text-xs text-muted-foreground sm:shrink-0 sm:self-center sm:text-right">
                  Historical record
                  <span className="block">Episode review is unavailable because the current episode is gone.</span>
                  {candidate.history_run_count != null && (
                    <span className="block">{candidate.history_run_count} processing history {candidate.history_run_count === 1 ? 'entry' : 'entries'}</span>
                  )}
                  {candidate.history_latest_processed_at && (
                    <span className="block">Last processed {new Date(candidate.history_latest_processed_at).toLocaleDateString()}</span>
                  )}
                </span>
              )}
            </div>
          ))}
        </fieldset>
      ) : (
        <p className="mt-4 text-sm text-warning">No matching feed is available.</p>
      )}

      {slug && (
        <label className="mt-4 flex items-start gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            className="mt-1"
          />
          <span>{selectedCandidate?.episode_available
            ? 'I checked the episode and confirm this feed.'
            : 'I reviewed the historical record and confirm this feed.'}</span>
        </label>
      )}

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          disabled={!slug || !confirmed || mutation.isPending}
          onClick={() => mutation.mutate()}
          className={`rounded px-3 py-2 text-sm ${btnPrimary} ${focusRing} disabled:opacity-50`}
        >
          {mutation.isPending ? 'Assigning...' : 'Assign correction'}
        </button>
        {mutation.error && (
          <p className="text-sm text-destructive">{(mutation.error as Error).message}</p>
        )}
        <button type="button" onClick={() => setConfirmDelete(true)} className={`rounded px-3 py-2 text-sm ${btnOutline} ${focusRing}`}>
          Delete
        </button>
      </div>
      {deleteMutation.error && <p className="mt-2 text-sm text-destructive">{(deleteMutation.error as Error).message}</p>}
      {confirmDelete && (
        <ConfirmModal
          title="Delete this saved correction?"
          confirmLabel="Delete"
          busyLabel="Deleting..."
          pending={deleteMutation.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => deleteMutation.mutate()}
        >
          <p>This permanently removes this saved correction record. It does not change source patterns, media, or feeds.</p>
        </ConfirmModal>
      )}
    </li>
  );
}

export default function UnresolvedCorrectionsPanel() {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [bulkSlug, setBulkSlug] = useState('');
  const [bulkAction, setBulkAction] = useState<'assign' | 'delete' | null>(null);
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['unresolved-corrections'],
    queryFn: getUnresolvedCorrections,
  });
  const { activeIds, commonCandidates } = useMemo(() => {
    const selected = data?.corrections.filter(
      (correction) => selectedIds.includes(correction.id),
    ) ?? [];
    const candidates = selected.length > 0
      ? selected[0].candidates.filter((candidate) => selected.every(
        (correction) => correction.candidates.some((other) => other.slug === candidate.slug),
      ))
      : [];
    return { activeIds: selected.map((correction) => correction.id), commonCandidates: candidates };
  }, [data?.corrections, selectedIds]);
  const validBulkSlug = commonCandidates.some((candidate) => candidate.slug === bulkSlug);
  const bulkMutation = useMutation({
    mutationFn: ({ action, correctionIds, slug }: {
      action: 'assign' | 'delete'; correctionIds: number[]; slug?: string;
    }) => bulkUpdateUnresolvedCorrections(action, correctionIds, slug),
    onSuccess: () => { setSelectedIds([]); setBulkSlug(''); setBulkAction(null); queryClient.invalidateQueries({ queryKey: ['unresolved-corrections'] }); },
  });
  const openBulkAction = (action: 'assign' | 'delete') => {
    bulkMutation.reset();
    setBulkAction(action);
  };
  const closeBulkAction = () => {
    bulkMutation.reset();
    setBulkAction(null);
  };
  const selectedFeed = commonCandidates.find((candidate) => candidate.slug === bulkSlug);
  const selectedLabel = `${activeIds.length} correction${activeIds.length === 1 ? '' : 's'}`;

  if (isLoading || (!error && !data?.count)) return null;

  return (
    <section className="mb-6 rounded-lg border border-warning/40 bg-warning/5 p-4">
      <button
        type="button"
        className={`flex w-full items-center gap-2 text-left ${focusRing}`}
        aria-expanded={isOpen}
        aria-controls="unassigned-corrections"
        onClick={() => setIsOpen((open) => !open)}
      >
        <h2 className="text-base font-semibold text-foreground">Unassigned corrections</h2>
        {data && (
          <span className="rounded-full bg-warning/20 px-2 py-0.5 text-xs font-medium text-warning">
            {data.count}
          </span>
        )}
        <svg className={`ml-auto h-5 w-5 shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {isOpen && (
        <div id="unassigned-corrections">
          <p className="mt-1 text-sm text-muted-foreground">
            Choose the feed that owns each legacy correction. The assignment changes saved review history.
          </p>
          {error ? (
            <p className="mt-3 text-sm text-destructive">Could not load unassigned corrections.</p>
          ) : (<>
            <ul className="mt-4 space-y-3">
              <li className="rounded border border-border bg-card p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span>{activeIds.length} selected</span>
                  <button type="button" onClick={() => setSelectedIds(data?.corrections.map((correction) => correction.id) ?? [])} className={`${btnOutline} min-h-11 rounded px-3 py-2 ${focusRing} touch-manipulation transition-colors sm:min-h-0`}>Select all</button>
                  <button type="button" onClick={() => setSelectedIds(data?.corrections.filter((correction) => correction.candidates.length === 0).map((correction) => correction.id) ?? [])} className={`${btnOutline} min-h-11 rounded px-3 py-2 ${focusRing} touch-manipulation transition-colors sm:min-h-0`}>Select unavailable</button>
                  <button type="button" onClick={() => setSelectedIds([])} className={`${btnOutline} min-h-11 rounded px-3 py-2 ${focusRing} touch-manipulation transition-colors sm:min-h-0`}>Clear</button>
                </div>
                {activeIds.length > 0 && <div className="mt-3 flex flex-wrap items-center gap-2">
                  <select aria-label="Feed for selected corrections" value={bulkSlug} onChange={(event) => setBulkSlug(event.target.value)} className={`min-h-11 sm:min-h-0 ${selectBase}`}>
                    <option value="">Choose one proven feed</option>
                    {commonCandidates.map((candidate) => <option key={candidate.slug} value={candidate.slug}>{candidate.podcast_title || candidate.slug}</option>)}
                  </select>
                  <button type="button" disabled={!validBulkSlug} onClick={() => openBulkAction('assign')} className={`${btnPrimary} min-h-11 rounded px-3 py-2 text-sm ${focusRing} touch-manipulation transition-colors disabled:opacity-50 sm:min-h-0`}>Assign selected</button>
                  <button type="button" onClick={() => openBulkAction('delete')} className={`${btnDestructive} min-h-11 rounded px-3 py-2 text-sm ${focusRing} touch-manipulation transition-colors sm:min-h-0`}>Delete selected</button>
                </div>}
              </li>
              {data?.corrections.map((correction) => (
                <CorrectionRow key={correction.id} correction={correction} selected={selectedIds.includes(correction.id)} onSelected={(checked) => setSelectedIds((ids) => checked ? [...ids, correction.id] : ids.filter((id) => id !== correction.id))} />
              ))}
            </ul>
            {bulkAction && <ConfirmModal title={bulkAction === 'assign' ? `Assign ${selectedLabel} to ${selectedFeed?.podcast_title || bulkSlug}?` : `Delete ${selectedLabel}?`} confirmLabel={bulkAction === 'assign' ? 'Assign' : 'Delete'} busyLabel={bulkAction === 'assign' ? 'Assigning...' : 'Deleting...'} pending={bulkMutation.isPending} onCancel={closeBulkAction} onConfirm={() => bulkMutation.mutate({ action: bulkAction, correctionIds: activeIds, slug: bulkAction === 'assign' ? bulkSlug : undefined })}>
              <p>{bulkAction === 'assign' ? 'The selected corrections will be assigned to this feed.' : 'This permanently removes the selected unassigned corrections. Patterns, media, and feeds are unchanged.'}</p>
              {bulkMutation.error && <p role="alert" className="mt-3 text-sm text-destructive">{(bulkMutation.error as Error).message}</p>}
            </ConfirmModal>}
          </>)}
        </div>
      )}
    </section>
  );
}
