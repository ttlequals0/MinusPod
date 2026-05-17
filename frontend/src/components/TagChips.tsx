import { memo } from 'react';

interface Props {
  tags: string[];
  variant?: 'sponsor' | 'podcast';
  className?: string;
}

const ACCENT_BY_TAG: Record<string, string> = {
  // Podcast genres
  news: 'bg-blue-500/15 text-blue-700 dark:text-blue-400',
  politics: 'bg-blue-600/15 text-blue-700 dark:text-blue-400',
  business: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  technology: 'bg-violet-500/15 text-violet-700 dark:text-violet-400',
  comedy: 'bg-pink-500/15 text-pink-700 dark:text-pink-400',
  true_crime: 'bg-red-500/15 text-red-700 dark:text-red-400',
  sports: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  science: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-400',
  health: 'bg-rose-500/15 text-rose-700 dark:text-rose-400',
  mental_health: 'bg-rose-600/15 text-rose-700 dark:text-rose-400',
};

const DEFAULT_ACCENT = 'bg-slate-500/15 text-slate-700 dark:text-slate-300';
const UNIVERSAL_ACCENT = 'bg-indigo-500/20 text-indigo-700 dark:text-indigo-400 border border-indigo-500/40';

function tagClass(tag: string): string {
  if (tag === 'universal') return UNIVERSAL_ACCENT;
  return ACCENT_BY_TAG[tag] || DEFAULT_ACCENT;
}

function TagChipsImpl({ tags, className = '' }: Props) {
  if (!tags || tags.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {tags.map((tag) => (
        <span
          key={tag}
          className={`px-2 py-0.5 text-xs rounded ${tagClass(tag)}`}
          title={tag === 'universal' ? 'Universal sponsor (matches every podcast)' : tag}
        >
          {tag === 'universal' ? '* universal' : tag}
        </span>
      ))}
    </div>
  );
}

export const TagChips = memo(TagChipsImpl);
