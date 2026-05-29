import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getTagVocabulary } from '../api/community';
import type { PatternOverride } from '../api/patterns';
import { expectedFilename } from '../api/communitySlug';
import { TagChips } from './TagChips';

interface Props {
  patternId: number;
  communityIdHint: string;
  baseSponsor: string;
  baseAliases: string[];
  baseTags: string[];
  override?: PatternOverride;
  onChange: (next: PatternOverride | undefined) => void;
}

function chips(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function PatternExportEditRow({
  patternId,
  communityIdHint,
  baseSponsor,
  baseAliases,
  baseTags,
  override,
  onChange,
}: Props) {
  const { data: vocab } = useQuery({
    queryKey: ['tagVocabulary'],
    queryFn: getTagVocabulary,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const sponsor = override?.sponsor ?? baseSponsor;
  const aliases = override?.sponsor_aliases ?? baseAliases;
  const tags = override?.sponsor_tags ?? baseTags;
  const filename = useMemo(
    () => expectedFilename(sponsor, communityIdHint) ?? '(filename unavailable)',
    [sponsor, communityIdHint],
  );
  const allowed = new Set(vocab?.all_tags ?? []);
  const unknownTags = tags.filter((t) => !allowed.has(t));

  function update(next: Partial<PatternOverride>) {
    const merged: PatternOverride = {
      sponsor: override?.sponsor ?? undefined,
      sponsor_aliases: override?.sponsor_aliases ?? undefined,
      sponsor_tags: override?.sponsor_tags ?? undefined,
      ...next,
    };
    // Sort before comparison so reordering aliases or tags (with no
    // semantic change) does not light the "override" badge.
    const sameList = (a: string[] | undefined, b: string[]) =>
      a === undefined || JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
    // The backend treats an empty / whitespace-only sponsor override as
    // "no override" and falls back to the DB name. Mirror that here so the
    // UI badge does not lie when the contributor clears the field.
    const trimmedSponsor = (merged.sponsor ?? '').trim();
    const sameSponsor =
      merged.sponsor === undefined ||
      trimmedSponsor === '' ||
      merged.sponsor === baseSponsor;
    const same =
      sameSponsor &&
      sameList(merged.sponsor_aliases, baseAliases) &&
      sameList(merged.sponsor_tags, baseTags);
    onChange(same ? undefined : merged);
  }

  const inputClass =
    'flex-1 rounded border border-border bg-background px-2 py-1 text-xs ' +
    'focus:outline-none focus:ring-1 focus:ring-ring';

  return (
    <div className="ml-6 mt-1 mb-2 p-3 rounded border border-border bg-muted/30 space-y-2 text-xs">
      <div className="flex flex-col sm:flex-row sm:items-center gap-2">
        <label
          className="w-24 shrink-0 text-muted-foreground"
          htmlFor={`sponsor-${patternId}`}
        >
          Sponsor
        </label>
        <input
          id={`sponsor-${patternId}`}
          type="text"
          className={inputClass}
          value={sponsor}
          onChange={(e) => update({ sponsor: e.target.value })}
        />
      </div>
      <div className="flex flex-col sm:flex-row sm:items-center gap-2">
        <label
          className="w-24 shrink-0 text-muted-foreground"
          htmlFor={`aliases-${patternId}`}
        >
          Aliases
        </label>
        <input
          id={`aliases-${patternId}`}
          type="text"
          placeholder="comma-separated"
          className={inputClass}
          value={aliases.join(', ')}
          onChange={(e) => update({ sponsor_aliases: chips(e.target.value) })}
        />
      </div>
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <label
          className="w-24 shrink-0 pt-1 text-muted-foreground"
          htmlFor={`tags-${patternId}`}
        >
          Tags
        </label>
        <div className="flex-1 space-y-1">
          <input
            id={`tags-${patternId}`}
            type="text"
            placeholder="comma-separated, e.g. universal, business"
            className={'w-full rounded border border-border bg-background px-2 py-1 text-xs ' +
              'focus:outline-none focus:ring-1 focus:ring-ring'}
            value={tags.join(', ')}
            onChange={(e) => update({ sponsor_tags: chips(e.target.value) })}
          />
          <TagChips tags={tags} />
          {unknownTags.length > 0 && (
            <p className="text-xs text-rose-600 dark:text-rose-400">
              Unknown tags will be rejected: {unknownTags.join(', ')}. See patterns/vocabulary.json.
            </p>
          )}
        </div>
      </div>
      <div className="flex flex-col sm:flex-row sm:items-center gap-2">
        <label className="w-24 shrink-0 text-muted-foreground">Filename</label>
        <code className="flex-1 font-mono text-xs px-2 py-1 rounded bg-background border border-border text-muted-foreground">
          {filename}
        </code>
      </div>
    </div>
  );
}
