import type { Episode } from '../api/types';

function relativeTime(iso: string | null, now: number): string {
  const ms = iso ? Date.parse(iso) - now : 0;
  const minutes = Math.round(ms / 60000);
  if (!(minutes > 0)) return 'now';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

export function sponsorBlockLabel(
  sb: Episode['sponsorBlock'] | undefined,
  probeAttempts?: number,
  now: number = Date.now(),
): string | null {
  if (!sb || !sb.state) return null;
  let label: string;
  switch (sb.state) {
    case 'waiting':
    case 'probing':
      label = `Waiting for SponsorBlock, attempt ${sb.attempts}${probeAttempts != null ? ` of ${probeAttempts}` : ''}, next check in ${relativeTime(sb.nextCheckAt, now)}`;
      break;
    case 'watching':
      label = `Watching SponsorBlock, next check in ${relativeTime(sb.nextCheckAt, now)}`;
      break;
    case 'done':
      label = `SponsorBlock: ${sb.segmentCount} segment${sb.segmentCount === 1 ? '' : 's'}`;
      break;
    case 'exhausted':
      label = 'SponsorBlock timed out, processed with LLM';
      break;
    case 'llm':
      label = 'No SponsorBlock marks, processed with LLM';
      break;
  }
  if (sb.note === 'full') label += ', marked whole video as sponsored';
  if (sb.lastError) label += ', last check failed';
  return label;
}
