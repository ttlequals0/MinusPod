// Backend merges gaps strictly below this; it also merges silent gaps up to 30 s the UI cannot detect.
export const AD_BLOCK_MERGE_GAP_S = 5;

export interface AdBlock<T extends { start: number; end: number }> {
  start: number;
  end: number;
  members: T[];
}

// Groups cut markers the audio processor merges into one span.
export function groupAdBlocks<T extends { start: number; end: number }>(markers: T[]): AdBlock<T>[] {
  const sorted = [...markers].sort((a, b) => a.start - b.start);
  const blocks: AdBlock<T>[] = [];
  for (const m of sorted) {
    const last = blocks[blocks.length - 1];
    if (last && m.start - last.end < AD_BLOCK_MERGE_GAP_S) {
      last.members.push(m);
      last.end = Math.max(last.end, m.end);
    } else {
      blocks.push({ start: m.start, end: m.end, members: [m] });
    }
  }
  return blocks;
}
