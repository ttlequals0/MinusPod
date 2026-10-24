import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { addNormalization, updateNormalization } from '../api/sponsors';
import { SponsorNormalization, NormalizationCategory } from '../api/types';
import { getErrorMessage } from '../api/client';
import { Modal, dialogFooterBtn } from './Modal';
import CloseButton from './CloseButton';
import { btnPrimary, btnOutline } from './buttonStyles';
import { inputBase, labelBase, selectBase } from './fieldStyles';

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
    onError: (e: unknown) => setError(getErrorMessage(e, 'Could not save normalization.')),
  });

  return (
    <Modal ariaLabel="Edit normalization" onClose={onClose} panelClassName="max-w-lg w-full"
      header={(
        <>
          <h2 className="text-lg font-semibold text-foreground">
            {isNew ? 'Add normalization' : 'Edit normalization'}
          </h2>
          <CloseButton onClick={onClose} />
        </>
      )}
      footer={(
        <>
          <button
            onClick={onClose}
            className={`${dialogFooterBtn} ${btnOutline}`}
          >
            Cancel
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className={`${dialogFooterBtn} ${btnPrimary}`}
          >
            {save.isPending ? 'Saving...' : isNew ? 'Add' : 'Save'}
          </button>
        </>
      )}
    >
      <div className="p-4 space-y-4">
        <div>
          <label className={labelBase}>
            Pattern <span className="text-muted-foreground font-normal">(regex)</span>
          </label>
          <input
            type="text"
            value={terms}
            onChange={(e) => setTerms(e.target.value)}
            className={`w-full ${inputBase} font-mono`}
            placeholder="(?i)acme\s+corp"
          />
        </div>

        <div>
          <label className={labelBase}>Replacement</label>
          <input
            type="text"
            value={canonical}
            onChange={(e) => setCanonical(e.target.value)}
            className={`w-full ${inputBase}`}
            placeholder="Acme"
          />
        </div>

        <div>
          <label className={labelBase}>Category</label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as NormalizationCategory)}
            className={`w-full ${selectBase}`}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
    </Modal>
  );
}

export default NormalizationEditModal;
