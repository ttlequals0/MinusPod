import { focusRing } from './fieldStyles';

// Data table recipe. History Processing is the reference; every data table
// and its card fallback share these so the pages cannot drift apart.
export const tableCard = 'bg-card border border-border rounded-lg overflow-hidden';
export const tableHead = 'bg-muted/50';
// Header type without padding or alignment, for SortHeader's padding override.
export const thBase = 'py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider';
export const th = `px-4 text-left ${thBase}`;
export const thRight = `px-4 text-right ${thBase}`;
export const tableBody = 'divide-y divide-border';
export const tableRow = 'hover:bg-muted/50';
export const td = 'px-4 py-3 text-sm';
export const tdMuted = `${td} text-muted-foreground`;
export const tdNum = `${tdMuted} text-right tabular-nums`;
export const tdEmpty = 'px-4 py-8 text-center text-muted-foreground';
// Card fallback for a table row below its table breakpoint.
export const rowCard = 'bg-card rounded-lg border border-border p-4';
// Podcast and episode links in data tables and card fallbacks.
export const tableLink =
  `text-foreground underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground ${focusRing}`;
