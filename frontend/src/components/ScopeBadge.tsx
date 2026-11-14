import { AdPattern } from '../api/patterns';
import { tint } from './badgeStyles';
import { Badge } from './Badge';

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
  let tone: keyof typeof tint;
  if (pattern.scope === 'global') {
    text = 'Global';
    tone = 'blue';
  } else if (pattern.scope === 'network') {
    text = `Network: ${pattern.network_id}`;
    tone = 'purple';
  } else if (pattern.scope === 'podcast') {
    text = pattern.podcast_name || 'Podcast';
    tone = 'success';
  } else {
    return null;
  }
  return <Badge tone={tone} title={text} wrap={!truncate}>{text}</Badge>;
}
