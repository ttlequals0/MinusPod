export interface TimedText {
  start: number;
  end: number;
  text: string;
  speaker?: unknown;
}

export interface Paragraph<T extends TimedText> {
  start: number;
  end: number;
  segments: T[];
}

const DEFAULT_GAP = 0.5;
const MIN_GAP = 0.25;
const MAX_GAP = 1.0;
const MIN_GAPS = 4;
// Break at the first sentence end past the target; never run past the hard cap.
const TARGET_PARAGRAPH_SECONDS = 20;
const MAX_PARAGRAPH_SECONDS = 45;
// Closing quotes include the curly forms (\u201d \u2019) that transcripts carry.
const SENTENCE_END = /[.!?]["'\u201d\u2019)\]]*\s*$/;

// Linear-interpolated percentile of an ascending array.
function percentile(sorted: number[], p: number): number {
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

export function gapThreshold(segs: readonly TimedText[]): number {
  const gaps: number[] = [];
  for (let i = 1; i < segs.length; i++) gaps.push(Math.max(0, segs[i].start - segs[i - 1].end));
  if (gaps.length < MIN_GAPS) return DEFAULT_GAP;
  const sorted = [...gaps].sort((a, b) => a - b);
  const p90 = percentile(sorted, 0.9);
  const kept = sorted.filter((g) => g <= p90);
  return Math.min(MAX_GAP, Math.max(MIN_GAP, percentile(kept, 0.5)));
}

export function groupParagraphs<T extends TimedText>(
  segs: readonly T[],
  threshold: number = gapThreshold(segs),
): Paragraph<T>[] {
  const out: Paragraph<T>[] = [];
  let cur: Paragraph<T> | null = null;
  for (const seg of segs) {
    if (cur) {
      const prev = cur.segments[cur.segments.length - 1];
      const gap = seg.start - prev.end;
      const speakerChange = 'speaker' in seg && 'speaker' in prev && seg.speaker !== prev.speaker;
      const dur = prev.end - cur.start;
      const brk = gap > threshold
        || (dur >= TARGET_PARAGRAPH_SECONDS && SENTENCE_END.test(prev.text))
        || dur >= MAX_PARAGRAPH_SECONDS
        || speakerChange;
      if (!brk) {
        cur.segments.push(seg);
        cur.end = Math.max(cur.end, seg.end);
        continue;
      }
    }
    cur = { start: seg.start, end: seg.end, segments: [seg] };
    out.push(cur);
  }
  return out;
}
