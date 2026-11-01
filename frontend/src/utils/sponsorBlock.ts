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

export function sponsorBlockSubmitLabel(sb: Episode['sponsorBlock'] | undefined): string | null {
  const subs = sb?.submissions;
  if (!subs || subs.length === 0) return null;
  const n = (c: number) => `${c} segment${c === 1 ? '' : 's'}`;
  const count = (s: string) => subs.filter((x) => x.status === s).length;
  const failed = subs.find((x) => ['rejected', 'rate_limited', 'error'].includes(x.status));
  if (failed) return `SponsorBlock submission ${failed.status.replace('_', ' ')}${failed.detail ? `: ${failed.detail}` : ''}`;
  if (count('submitted')) return `Submitted to SponsorBlock: ${n(count('submitted'))}`;
  return `Already on SponsorBlock: ${n(count('duplicate'))}`;
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
      if (sb.nextCheckAt == null) {
        label = 'Waiting for SponsorBlock, queued';
        break;
      }
      label = `Waiting for SponsorBlock, attempt ${sb.attempts}${probeAttempts != null ? ` of ${probeAttempts}` : ''}, next check in ${relativeTime(sb.nextCheckAt, now)}`;
      break;
    case 'watching':
      label = `Watching SponsorBlock, next check in ${relativeTime(sb.nextCheckAt, now)}`;
      break;
    case 'done':
      label = `SponsorBlock: ${sb.segmentCount} segment${sb.segmentCount === 1 ? '' : 's'}`;
      break;
    case 'exhausted':
      if (sb.note?.split(',').includes('no_captions')) return 'No marks and no captions, served unmodified';
      label = 'SponsorBlock timed out, processed with LLM';
      break;
    case 'llm':
      label = 'No SponsorBlock marks, processed with LLM';
      break;
    default:
      return null;
  }
  const flags = sb.note?.split(',') ?? [];
  if (flags.includes('full')) label += ', marked whole video as sponsored';
  if (flags.includes('no_captions')) label += ', no captions (transcription off)';
  if (sb.lastError) label += ', last check failed';
  return label;
}
