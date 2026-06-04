import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addSponsor, updateSponsor } from '../api/sponsors';
import { getTagVocabulary, updateSponsorTags } from '../api/community';
import { Sponsor } from '../api/types';

interface Props {
  // null = create a new sponsor
  sponsor: Sponsor | null;
  onClose: () => void;
  onSaved: () => void;
}

function SponsorEditModal({ sponsor, onClose, onSaved }: Props) {
  const queryClient = useQueryClient();
  const isNew = sponsor === null;

  const [name, setName] = useState(sponsor?.name ?? '');
  const [aliasesText, setAliasesText] = useState((sponsor?.aliases ?? []).join(', '));
  const [category, setCategory] = useState(sponsor?.category ?? '');
  const [tags, setTags] = useState<string[]>(sponsor?.tags ?? []);
  const [isActive, setIsActive] = useState(sponsor?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const { data: vocab } = useQuery({
    queryKey: ['tagVocabulary'],
    queryFn: getTagVocabulary,
  });

  const parseAliases = () =>
    aliasesText.split(',').map((a) => a.trim()).filter(Boolean);

  const save = useMutation({
    mutationFn: async () => {
      const trimmed = name.trim();
      if (!trimmed) throw new Error('Name is required');
      const aliases = parseAliases();
      const cat = category.trim() || undefined;

      if (isNew) {
        const created = await addSponsor({ name: trimmed, aliases, category: cat });
        if (tags.length > 0) await updateSponsorTags(created.id, tags);
      } else {
        await updateSponsor(sponsor!.id, {
          name: trimmed,
          aliases,
          category: cat,
          is_active: isActive,
        });
        await updateSponsorTags(sponsor!.id, tags);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sponsors'] });
      onSaved();
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Failed to save sponsor'),
  });

  const toggleTag = (tag: string) =>
    setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-card border border-border rounded-lg shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-lg font-semibold text-foreground">
            {isNew ? 'Add Sponsor' : `Edit ${sponsor!.name}`}
          </h2>
          <button onClick={onClose} className="p-1 text-muted-foreground hover:text-foreground">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-secondary border border-border rounded"
              placeholder="Sponsor name"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-1">
              Aliases <span className="text-muted-foreground font-normal">(comma-separated)</span>
            </label>
            <input
              type="text"
              value={aliasesText}
              onChange={(e) => setAliasesText(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-secondary border border-border rounded"
              placeholder="alt name, abbreviation"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-1">Category</label>
            <input
              type="text"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-secondary border border-border rounded"
              placeholder="e.g. technology"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">Tags</label>
            <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
              {(vocab?.all_tags ?? []).map((tag) => {
                const on = tags.includes(tag);
                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => toggleTag(tag)}
                    className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                      on
                        ? 'bg-primary/20 text-primary border-primary/40'
                        : 'bg-secondary text-muted-foreground border-border hover:bg-accent'
                    }`}
                  >
                    {tag}
                  </button>
                );
              })}
            </div>
          </div>

          {!isNew && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
                className="rounded"
              />
              <span className="text-sm text-foreground">Active</span>
              <span className="text-xs text-muted-foreground">
                (inactive sponsors are excluded from detection)
              </span>
            </label>
          )}

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
            {save.isPending ? 'Saving...' : isNew ? 'Add Sponsor' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default SponsorEditModal;
