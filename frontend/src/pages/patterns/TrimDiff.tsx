export interface TrimParts {
  before: string;
  kept: string;
  after: string;
}

// Folds text to a comparison form while recording, for each folded char, the
// index of the original char it came from.
function fold(text: string, keep: (ch: string) => boolean): { folded: string; map: number[] } {
  let folded = '';
  const map: number[] = [];
  let pendingSpace = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (!keep(ch)) {
      pendingSpace = folded.length > 0;
      continue;
    }
    if (pendingSpace) {
      folded += ' ';
      map.push(i);
      pendingSpace = false;
    }
    folded += ch.toLowerCase();
    map.push(i);
  }
  return { folded, map };
}

const notSpace = (ch: string) => !/\s/.test(ch);
const alnum = (ch: string) => /[\p{L}\p{N}]/u.test(ch);

/** Splits the original into the removed lead, the kept copy and the removed tail. */
export function splitTrim(original: string, kept: string): TrimParts | null {
  // Whitespace and case first; the backend accepts near-substrings, so fall
  // back to ignoring punctuation too.
  for (const keep of [notSpace, alnum]) {
    const o = fold(original, keep);
    const k = fold(kept, keep).folded;
    if (!k) continue;
    const at = o.folded.indexOf(k);
    if (at < 0) continue;
    const start = o.map[at];
    const end = o.map[at + k.length - 1] + 1;
    return {
      before: original.slice(0, start),
      kept: original.slice(start, end),
      after: original.slice(end),
    };
  }
  return null;
}

const removedCls = 'text-muted-foreground line-through decoration-destructive/70';
// Not <mark>: the app reserves mark's yellow for search hits.
const keptCls = 'bg-success/15 text-foreground rounded-sm';

export function TrimDiff({ original, kept }: { original: string; kept: string }) {
  const parts = splitTrim(original, kept);
  const box = 'text-sm leading-relaxed whitespace-pre-wrap break-words max-h-60 overflow-y-auto';
  if (!parts) {
    // Not locatable: show both texts whole rather than a wrong diff.
    return (
      <div className="space-y-2">
        <p className={box}><span className="sr-only">Removed: </span><del className={removedCls}>{original}</del></p>
        <p className={box}><span className="sr-only">Kept: </span><span data-diff="kept" className={keptCls}>{kept}</span></p>
      </div>
    );
  }
  return (
    <p className={box}>
      {parts.before && <><span className="sr-only">Removed: </span><del className={removedCls}>{parts.before}</del></>}
      <span className="sr-only">Kept: </span><span data-diff="kept" className={keptCls}>{parts.kept}</span>
      {parts.after && <><span className="sr-only">Removed: </span><del className={removedCls}>{parts.after}</del></>}
    </p>
  );
}
