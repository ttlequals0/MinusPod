import { memo } from 'react';
import { badgeBase, tint } from './badgeStyles';

interface Props {
  tags: string[];
  className?: string;
}

// Seven theme hues cannot distinguish 48 tags, so only the universal tag is accented.
const UNIVERSAL_ACCENT = `${tint.blue} border border-c-blue/40`;

function TagChipsImpl({ tags, className = '' }: Props) {
  if (!tags || tags.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {tags.map((tag) => (
        <span
          key={tag}
          className={`${badgeBase} ${tag === 'universal' ? UNIVERSAL_ACCENT : tint.neutral}`}
          title={tag === 'universal' ? 'Universal sponsor (matches every podcast)' : tag}
        >
          {tag === 'universal' ? '* universal' : tag}
        </span>
      ))}
    </div>
  );
}

export const TagChips = memo(TagChipsImpl);
