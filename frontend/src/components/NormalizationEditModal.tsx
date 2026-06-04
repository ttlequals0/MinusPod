import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { addNormalization, updateNormalization } from '../api/sponsors';
import { SponsorNormalization, NormalizationCategory } from '../api/types';

const CATEGORIES: NormalizationCategory[] = ['sponsor', 'url', 'number', 'phrase'];

interface Props {
  // null = create a new normalization
  normalization: SponsorNormalization | null;
  onClose: () => void;
  onSaved: () => void;
}

function NormalizationEditModal({ normalization, onClose, onSaved }: Props) {
  const queryClient = useQueryClient();
  const isNew = normalization === null;

  const [terms, setTerms] = useState(normalization?.terms ?? '');
  const [canonical, setCanonical] = useState(normalization?.canonical ?? '');
  const [category, setCategory] = useState<NormalizationCategory>(
    normalization?.category ?? 'sponsor'
  );
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const t = terms.trim();
      const c = canonical.trim();
      if (!t) throw new Error('Pattern is required');
      if (!c) throw new Error('Replacement is required');
      if (isNew) {
        await addNormalization({ terms: t, canonical: c, category });
      } else {
        await updateNormalization(normalization!.id, { terms: t, canonical: c, category });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['normalizations'] });
      onSaved();
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to save normalization'),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-card border border-border rounded-lg shadow-xl max-w-lg w-full">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-lg font-semibold text-foreground">
            {isNew ? 'Add Normalization' : 'Edit Normalization'}
          </h2>
          <button onClick={onClose} className="p-1 text-muted-foreground hover:text-foreground">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-1">
              Pattern <span className="text-muted-foreground font-normal">(regex)</span>
            </label>
            <input
              type="text"
              value={terms}
              onChange={(e) => setTerms(e.target.value)}
              className="w-full px-3 py-2 text-sm font-mono bg-secondary border border-border rounded"
              placeholder="(?i)acme\s+corp"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-1">Replacement</label>
            <input
              type="text"
              value={canonical}
              onChange={(e) => setCanonical(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-secondary border border-border rounded"
              placeholder="Acme"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-1">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as NormalizationCategory)}
              className="w-full px-3 py-2 text-sm bg-secondary border border-border rounded"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded border border-border hover:bg-accent transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {save.isPending ? 'Saving...' : isNew ? 'Add' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default NormalizationEditModal;
