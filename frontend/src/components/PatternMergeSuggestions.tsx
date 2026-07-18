import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getMergeSuggestions,
  mergePatterns,
  type MergeSuggestion,
} from '../api/patterns';
import { btnPrimary } from './buttonStyles';
import { getErrorMessage } from '../api/client';

// Same-sponsor near-duplicate clusters the backend precomputes (#399). The
// frontend only renders them and triggers the fold; it never computes
// similarity itself.

function SuggestionCard({
  suggestion,
  onMerged,
}: {
  suggestion: MergeSuggestion;
  onMerged: () => void;
}) {
  const queryClient = useQueryClient();
  const keepId = suggestion.suggested_keep_id;
  // Every member is folded by default; the user can drop individual rows. The
  // keep target is always kept.
  const [included, setIncluded] = useState<Set<number>>(
    () => new Set(suggestion.pattern_ids),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const mergeIds = suggestion.pattern_ids.filter(
    (id) => id !== keepId && included.has(id),
  );

  const toggle = (id: number) => {
    if (id === keepId) return; // keep target cannot be dropped
    setIncluded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const doMerge = async () => {
    if (mergeIds.length === 0) return;
    setBusy(true);
    setError(null);
    setWarning(null);
    try {
      const res = await mergePatterns({ keep_id: keepId, merge_ids: mergeIds });
      setWarning(res.warning ?? null);
      await queryClient.invalidateQueries({ queryKey: ['merge-suggestions'] });
      onMerged();
    } catch (e) {
      setError(getErrorMessage(e, 'Merge failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-border p-3 bg-secondary/30">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-2">
        <span className="text-sm font-medium text-foreground">
          {suggestion.count} similar {suggestion.sponsor || 'unknown-sponsor'} patterns
        </span>
        <button
          onClick={doMerge}
          disabled={busy || mergeIds.length === 0}
          className={`px-3 py-1.5 text-sm rounded-lg font-medium ${btnPrimary} disabled:opacity-50`}
        >
          {busy
            ? 'Merging...'
            : mergeIds.length === 0
              ? 'Select rows to merge'
              : `Merge ${mergeIds.length + 1} into 1`}
        </button>
      </div>
      <p className="text-xs text-muted-foreground mb-2">
        Result keeps pattern {keepId} and adds {suggestion.result_intro_variant_count} intro
        and {suggestion.result_outro_variant_count} outro variant(s).
      </p>
      <ul className="space-y-1">
        {suggestion.members.map((m) => (
          <li key={m.id} className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={m.id === keepId || included.has(m.id)}
              disabled={m.id === keepId}
              onChange={() => toggle(m.id)}
              className="mt-0.5"
              aria-label={`Include pattern ${m.id} in the fold`}
            />
            <span className="text-muted-foreground">
              <span className="font-mono text-foreground">#{m.id}</span>
              {m.id === keepId && <span className="ml-1 text-primary">(keep)</span>}
              <span className="ml-2">{m.text_template}</span>
            </span>
          </li>
        ))}
      </ul>
      {warning && (
        <p className="mt-2 text-xs text-yellow-600 dark:text-yellow-400">{warning}</p>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}

export default function PatternMergeSuggestions({ onMerged }: { onMerged: () => void }) {
  const { data: suggestions, isLoading } = useQuery({
    queryKey: ['merge-suggestions'],
    queryFn: getMergeSuggestions,
  });

  if (isLoading || !suggestions || suggestions.length === 0) return null;

  return (
    <div className="bg-card rounded-lg border border-border p-4 mb-6">
      <h2 className="text-sm font-medium text-foreground mb-1">Merge suggestions</h2>
      <p className="text-xs text-muted-foreground mb-3">
        Same-sponsor patterns that look like the same ad read. Folding keeps one row
        and adds the others as intro/outro variants.
      </p>
      <div className="space-y-3">
        {suggestions.map((s) => (
          <SuggestionCard
            key={`${s.suggested_keep_id}-${s.pattern_ids.join(',')}`}
            suggestion={s}
            onMerged={onMerged}
          />
        ))}
      </div>
    </div>
  );
}
