import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateFeed } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import Checkbox from '../../components/Checkbox';
import { btnLg, btnPrimary, btnSecondary } from '../../components/buttonStyles';
import { inputBase, labelBase, textareaBase } from '../../components/fieldStyles';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';

export type MetadataField = 'author' | 'explicit' | 'categories' | 'title' | 'description';

export interface MetadataValues {
  title: string;
  description: string;
  author: string;
  explicit: boolean;
  categoriesInput: string;
}

export const metadataValuesFromFeed = (f: Feed): MetadataValues => ({
  title: f.title,
  description: f.description ?? '',
  author: f.author ?? '',
  explicit: f.explicit ?? false,
  categoriesInput: (f.categories ?? []).join(', '),
});

export const parseCategories = (input: string) => input.split(',').map((c) => c.trim()).filter(Boolean);

// Controlled inputs shared by the local panel (inside its own form) and FeedMetadataForm.
export function FeedMetadataFields({ slug, idPrefix, fields, values, onChange }: {
  slug: string;
  idPrefix: string;
  fields: MetadataField[];
  values: MetadataValues;
  onChange: (patch: Partial<MetadataValues>) => void;
}) {
  const has = (f: MetadataField) => fields.includes(f);
  return (
    <>
      {has('title') && (
        <div>
          <label htmlFor={`${idPrefix}-title-${slug}`} className={labelBase}>Title</label>
          <input
            id={`${idPrefix}-title-${slug}`}
            type="text"
            value={values.title}
            onChange={(e) => onChange({ title: e.target.value })}
            required
            className={`w-full ${inputBase}`}
          />
        </div>
      )}
      {has('description') && (
        <div>
          <label htmlFor={`${idPrefix}-description-${slug}`} className={labelBase}>Description</label>
          <textarea
            id={`${idPrefix}-description-${slug}`}
            value={values.description}
            onChange={(e) => onChange({ description: e.target.value })}
            rows={6}
            className={`w-full ${textareaBase}`}
          />
        </div>
      )}
      {has('author') && (
        <div>
          <label htmlFor={`${idPrefix}-author-${slug}`} className={labelBase}>Author</label>
          <input
            id={`${idPrefix}-author-${slug}`}
            type="text"
            value={values.author}
            onChange={(e) => onChange({ author: e.target.value })}
            className={`w-full ${inputBase}`}
          />
        </div>
      )}
      {has('explicit') && (
        <Checkbox
          checked={values.explicit}
          onChange={(explicit) => onChange({ explicit })}
          label="Explicit content"
        />
      )}
      {has('categories') && (
        <div>
          <label htmlFor={`${idPrefix}-categories-${slug}`} className={labelBase}>Categories</label>
          <input
            id={`${idPrefix}-categories-${slug}`}
            type="text"
            value={values.categoriesInput}
            onChange={(e) => onChange({ categoriesInput: e.target.value })}
            placeholder="Technology, Business"
            className={`w-full ${inputBase}`}
            aria-describedby={`${idPrefix}-categories-${slug}-help`}
          />
          <p id={`${idPrefix}-categories-${slug}-help`} className="mt-1 text-sm text-muted-foreground">
            Comma-separated, e.g. Technology, Business
          </p>
        </div>
      )}
    </>
  );
}

// null clears the field server-side; undefined would drop the key
const buildMetadataPatch = (values: MetadataValues, fields: MetadataField[]) => {
  const categories = parseCategories(values.categoriesInput);
  const all = {
    author: values.author.trim() || null,
    explicit: values.explicit,
    categories: categories.length ? categories : null,
  };
  return Object.fromEntries(Object.entries(all).filter(([k]) => fields.includes(k as MetadataField)));
};

// Self-saving metadata form for panels without their own form (YouTube feeds).
function FeedMetadataForm({ feed, slug, fields }: { feed: Feed; slug: string; fields: MetadataField[] }) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<MetadataValues>(() => metadataValuesFromFeed(feed));
  const [baseline, setBaseline] = useState(values);
  const dirty = JSON.stringify(buildMetadataPatch(values, fields)) !== JSON.stringify(buildMetadataPatch(baseline, fields));
  useUnsavedChanges(dirty);
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(savedTimer.current), []);
  useSyncFromQuery(feed, (f) => {
    if (dirty) return;
    const next = metadataValuesFromFeed(f);
    setValues(next);
    setBaseline(next);
  });

  const mutation = useMutation({
    mutationFn: () => updateFeed(slug, buildMetadataPatch(values, fields)),
    onSuccess: (updated) => {
      const next = metadataValuesFromFeed(updated);
      setValues(next);
      setBaseline(next);
      queryClient.setQueryData(['feed', slug], updated);
      setSaved(true);
      clearTimeout(savedTimer.current);
      savedTimer.current = setTimeout(() => setSaved(false), 2000);
    },
  });

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }}
      className="space-y-4"
    >
      <fieldset disabled={mutation.isPending} className="space-y-4">
        <FeedMetadataFields
          slug={slug}
          idPrefix="feed-meta"
          fields={fields}
          values={values}
          onChange={(patch) => { setSaved(false); setValues((v) => ({ ...v, ...patch })); }}
        />
        {mutation.isError && (
          <p role="alert" className="text-sm text-destructive">{getErrorMessage(mutation.error, 'Could not save this feed.')}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" disabled={!dirty} className={`${btnLg} ${btnPrimary}`}>
            {mutation.isPending ? 'Saving...' : 'Save metadata'}
          </button>
          {dirty && <button type="button" className={`${btnLg} ${btnSecondary}`} onClick={() => {
            const next = metadataValuesFromFeed(feed);
            setValues(next); setBaseline(next); setSaved(false); mutation.reset();
          }}>Cancel</button>}
          {saved && <span role="status" className="text-sm text-success">Metadata saved.</span>}
        </div>
      </fieldset>
    </form>
  );
}

export default FeedMetadataForm;
