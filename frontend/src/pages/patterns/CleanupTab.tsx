import { useState } from 'react';
import { Link } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import {
  approveCleanupSuggestion, bulkCleanupSuggestions, getPatternCleanupSuggestions,
  patternCleanupQueryKey, rejectCleanupSuggestion, undoCleanupSuggestion,
  type CleanupKind, type CleanupStatus, type FlagPayload, type PatternCleanupSuggestion,
  type RenamePayload, type RetirePayload, type SplitPayload, type TrimPayload,
} from '../../api/patternCleanup';
import { ApiError, getErrorMessage } from '../../api/client';
import SegmentedToggle from '../../components/SegmentedToggle';
import Checkbox from '../../components/Checkbox';
import { ScopeBadge } from '../../components/ScopeBadge';
import { SkeletonRows } from '../../components/Skeleton';
import { badgeBase, tint } from '../../components/badgeStyles';
import { btnOutline, btnPrimary } from '../../components/buttonStyles';
import { rowActionBtn } from '../../components/rowActionStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';
import { formatDate } from '../../utils/format';
import { TrimDiff } from './TrimDiff';

type KindFilter = CleanupKind | 'all';

const KIND_OPTIONS: Array<{ value: KindFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'trim', label: 'Trim' },
  { value: 'split', label: 'Split' },
  { value: 'rename', label: 'Rename' },
  { value: 'retire', label: 'Retire' },
  { value: 'flag', label: 'Flag' },
];

const KIND_BADGE: Record<CleanupKind, [string, string]> = {
  trim: ['Trim', tint.teal],
  split: ['Split', tint.purple],
  rename: ['Rename', tint.blue],
  retire: ['Retire', tint.neutral],
  flag: ['Flag', tint.warning],
};

const STATUS_OPTIONS: Array<[CleanupStatus, string]> = [
  ['pending', 'Pending'],
  ['approved', 'Approved'],
  ['rejected', 'Rejected'],
  ['undone', 'Undone'],
];

const STATUS_BADGE: Record<Exclude<CleanupStatus, 'pending'>, [string, string]> = {
  approved: ['Approved', tint.success],
  rejected: ['Rejected', tint.secondary],
  undone: ['Undone', tint.neutral],
};

// Below sm the two actions share the card width; from sm they size to content.
const actionBtn = `${rowActionBtn} grow basis-0 sm:grow-0 sm:basis-auto ${focusRing}`;

function actionError(err: unknown): string {
  if (err instanceof ApiError && err.status === 409) {
    return 'This suggestion cannot be applied: the pattern changed or the suggestion was already handled.';
  }
  return getErrorMessage(err, 'The action failed.');
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function ProposedChange({ s }: { s: PatternCleanupSuggestion }) {
  const original = s.before?.textTemplate ?? '';
  if (s.kind === 'trim') {
    return <TrimDiff original={original} kept={(s.payload as TrimPayload).text} />;
  }
  if (s.kind === 'split') {
    const { pieces } = s.payload as SplitPayload;
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        {pieces.map((piece, i) => (
          <div key={i} data-testid="split-piece" className="rounded border border-border bg-muted/40 p-3 min-w-0">
            <span className={`${badgeBase} ${tint.secondary}`}>{piece.sponsor}</span>
            <p className="mt-2 text-sm leading-relaxed whitespace-pre-wrap break-words">{piece.text}</p>
          </div>
        ))}
      </div>
    );
  }
  if (s.kind === 'rename') {
    const from = s.before?.sponsor ?? s.pattern?.sponsor ?? '(Unknown)';
    return (
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground line-through">{from}</span>
        <ArrowRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">to</span>
        <span className="font-medium text-foreground">{(s.payload as RenamePayload).sponsor}</span>
      </p>
    );
  }
  if (s.kind === 'retire') {
    const p = s.payload as RetirePayload;
    return (
      <p className="text-sm text-foreground">
        No matches in {p.unusedDays} days.{' '}
        <span className="text-muted-foreground">
          Last matched {p.lastMatchedAt ? formatDate(p.lastMatchedAt) : 'never'}, created{' '}
          {formatDate(s.pattern?.createdAt ?? null)}, {plural(p.confirmationCount, 'confirmation')}.
        </span>
      </p>
    );
  }
  const p = s.payload as FlagPayload;
  return (
    <div className="space-y-2 text-sm">
      <p className="text-foreground">
        {plural(p.falsePositiveCount, 'false positive')} against {plural(p.confirmationCount, 'confirmation')}.
      </p>
      {p.contaminated && p.contaminationReason && (
        <p className="text-warning">{p.contaminationReason}</p>
      )}
      <p className="text-foreground">
        <span className="text-muted-foreground">Recommended: </span>
        {p.recommended === 'trim' ? 'Trim to the ad copy.' : 'Disable the pattern.'}
      </p>
      {p.recommended === 'trim' && p.trimText && <TrimDiff original={original} kept={p.trimText} />}
    </div>
  );
}

function SuggestionCard({ s, selected, onSelect, onAction, busy }: {
  s: PatternCleanupSuggestion;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onAction: (action: 'approve' | 'reject' | 'undo') => void;
  busy: boolean;
}) {
  const [kindLabel, kindCls] = KIND_BADGE[s.kind];
  const pattern = s.pattern;
  const sponsor = pattern?.sponsor ?? s.before?.sponsor ?? null;
  return (
    <div
      data-testid={`cleanup-suggestion-${s.id}`}
      role="listitem"
      className="bg-card rounded-lg border border-border p-4 space-y-3"
    >
      <div className="flex items-start gap-3">
        {s.status === 'pending' && (
          <Checkbox
            checked={selected}
            onChange={onSelect}
            ariaLabel={`Select suggestion ${s.id}`}
            className="mt-0.5"
          />
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`${badgeBase} ${kindCls}`}>{kindLabel}</span>
            <span className="text-sm font-medium text-foreground min-w-0 break-words">
              {sponsor || '(Unknown)'}
            </span>
            {pattern && (
              <ScopeBadge
                pattern={{
                  scope: pattern.scope,
                  network_id: pattern.networkId,
                  podcast_name: pattern.podcastTitle,
                }}
                podcastClassName="truncate max-w-full"
              />
            )}
            {pattern && !pattern.isActive && (
              <span className={`${badgeBase} ${tint.destructive}`}>Inactive</span>
            )}
            {s.status !== 'pending' && (
              <span className={`${badgeBase} ${STATUS_BADGE[s.status][1]}`}>
                {STATUS_BADGE[s.status][0]}
                {s.reviewedAt ? ` ${formatDate(s.reviewedAt)}` : ''}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="font-mono">#{s.patternId}</span>
            {s.confidence != null && (
              <span className="tabular-nums">Confidence {Math.round(s.confidence * 100)}%</span>
            )}
            <span>Suggested {formatDate(s.createdAt)}</span>
          </div>
        </div>
      </div>

      <ProposedChange s={s} />

      {s.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-0.5">
          {s.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        {pattern && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="text-success">Confirmed: {pattern.confirmationCount}</span>
            <span className={pattern.falsePositiveCount > 0 ? 'text-destructive' : ''}>
              False Pos: {pattern.falsePositiveCount}
            </span>
            <span>Last matched {formatDate(pattern.lastMatchedAt)}</span>
          </div>
        )}
        <div className="flex gap-2 sm:ml-auto">
          {s.status === 'pending' && (
            <>
              <button type="button" disabled={busy} onClick={() => onAction('approve')}
                className={`${actionBtn} ${btnPrimary}`}>
                Approve
              </button>
              <button type="button" disabled={busy} onClick={() => onAction('reject')}
                className={`${actionBtn} ${btnOutline}`}>
                Reject
              </button>
            </>
          )}
          {s.status === 'approved' && (
            <button type="button" disabled={busy} onClick={() => onAction('undo')}
              className={`${actionBtn} ${btnOutline}`}>
              Undo
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const ACTIONS = {
  approve: approveCleanupSuggestion,
  reject: rejectCleanupSuggestion,
  undo: undoCleanupSuggestion,
};

export default function CleanupTab() {
  const [kind, setKind] = useState<KindFilter>('all');
  const [status, setStatus] = useState<CleanupStatus>('pending');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: [...patternCleanupQueryKey, 'suggestions', status, kind],
    queryFn: () => getPatternCleanupSuggestions({
      status, kind: kind === 'all' ? undefined : kind, limit: 200,
    }),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: patternCleanupQueryKey });
    queryClient.invalidateQueries({ queryKey: ['patterns'] });
  };

  const single = useMutation({
    mutationFn: ({ id, action }: { id: number; action: keyof typeof ACTIONS }) => ACTIONS[action](id),
    onMutate: () => setMessage(null),
    onSuccess: refresh,
    onError: (err) => setMessage(actionError(err)),
  });

  const bulk = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: 'approve' | 'reject' }) =>
      bulkCleanupSuggestions(ids, action),
    onMutate: () => setMessage(null),
    onSuccess: (results, { ids, action }) => {
      const failed = results.filter((r) => 'error' in r).length;
      if (failed > 0) {
        setMessage(`${failed} of ${plural(ids.length, 'suggestion')} could not be ${
          action === 'approve' ? 'applied' : 'rejected'}: the pattern changed or the suggestion was already handled.`);
      }
      setSelected(new Set());
      refresh();
    },
    onError: (err) => setMessage(actionError(err)),
  });

  const changeFilter = (next: { kind?: KindFilter; status?: CleanupStatus }) => {
    if (next.kind) setKind(next.kind);
    if (next.status) setStatus(next.status);
    setSelected(new Set());
    setMessage(null);
  };

  const items = data ?? [];
  const pendingIds = items.filter((s) => s.status === 'pending').map((s) => s.id);
  const chosen = pendingIds.filter((id) => selected.has(id));
  const allChosen = pendingIds.length > 0 && chosen.length === pendingIds.length;
  const busy = single.isPending || bulk.isPending;

  const toggle = (id: number, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id); else next.delete(id);
      return next;
    });
  };

  return (
    <div>
      <div className="bg-card rounded-lg border border-border p-4 mb-6 flex flex-wrap gap-4 items-center">
        <SegmentedToggle
          variant="toolbar"
          ariaLabel="Kind"
          options={KIND_OPTIONS}
          value={kind}
          onChange={(v) => changeFilter({ kind: v })}
        />
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor="cleanup-status" className="text-sm text-muted-foreground shrink-0">Status</label>
          <select
            id="cleanup-status"
            value={status}
            onChange={(e) => changeFilter({ status: e.target.value as CleanupStatus })}
            className={`flex-1 sm:flex-none min-w-0 ${selectBase}`}
          >
            {STATUS_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      </div>

      {pendingIds.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <Checkbox
            checked={allChosen}
            onChange={(checked) => setSelected(checked ? new Set(pendingIds) : new Set())}
            label="Select all"
            labelClassName="text-sm text-muted-foreground"
          />
          <div className="flex gap-2 w-full sm:w-auto sm:ml-auto">
            <button
              type="button"
              disabled={busy || chosen.length === 0}
              onClick={() => bulk.mutate({ ids: chosen, action: 'approve' })}
              className={`${actionBtn} ${btnPrimary}`}
            >
              Approve selected ({chosen.length})
            </button>
            <button
              type="button"
              disabled={busy || chosen.length === 0}
              onClick={() => bulk.mutate({ ids: chosen, action: 'reject' })}
              className={`${actionBtn} ${btnOutline}`}
            >
              Reject selected ({chosen.length})
            </button>
          </div>
        </div>
      )}

      {message && <div role="alert" className="text-destructive text-sm mb-3">{message}</div>}
      {isLoading && <SkeletonRows count={4} />}
      {error && <div className="text-destructive text-sm">Failed to load suggestions.</div>}
      {!isLoading && !error && items.length === 0 && (
        <div className="bg-card rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
          {status === 'pending' && kind === 'all' ? (
            <>
              No suggestions. Run cleanup from{' '}
              <Link to="/settings" className={`text-primary hover:underline ${focusRing}`}>
                {'Settings > Experiments'}
              </Link>
              .
            </>
          ) : 'No suggestions match the current filters.'}
        </div>
      )}
      {items.length > 0 && (
        <div role="list" aria-label="Cleanup suggestions" className="space-y-3">
          {items.map((s) => (
            <SuggestionCard
              key={s.id}
              s={s}
              selected={selected.has(s.id)}
              onSelect={(checked) => toggle(s.id, checked)}
              onAction={(action) => single.mutate({ id: s.id, action })}
              busy={busy}
            />
          ))}
        </div>
      )}
    </div>
  );
}
