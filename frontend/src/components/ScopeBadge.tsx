import { AdPattern } from '../api/patterns';
import { badgeBase, tint } from './badgeStyles';

// Pattern scope badge shared by PatternsPage and PatternDetailModal. `truncate` lets list
// rows cut a long name with an ellipsis; the title always carries the full text.
export function ScopeBadge({
  pattern,
  truncate = false,
}: {
  pattern: Pick<AdPattern, 'scope' | 'network_id' | 'podcast_name'>;
  truncate?: boolean;
}) {
  let text: string;
  let toneClass: string;
  if (pattern.scope === 'global') {
    text = 'Global';
    toneClass = tint.blue;
  } else if (pattern.scope === 'network') {
    text = `Network: ${pattern.network_id}`;
    toneClass = tint.purple;
  } else if (pattern.scope === 'podcast') {
    text = pattern.podcast_name || 'Podcast';
    toneClass = tint.success;
  } else {
    return null;
  }
  return (
    <span className={`${badgeBase} ${toneClass} max-w-full${truncate ? '' : ' whitespace-normal! break-words'}`} title={text}>
      {truncate ? <span className="truncate">{text}</span> : text}
    </span>
  );
}
