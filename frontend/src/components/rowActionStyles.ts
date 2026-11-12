import { focusRing } from './fieldStyles';

// Sizing for the action buttons in a marker/detection row, and for the icon
// button that sits beside them. These are paired on purpose: a fixed icon
// size cannot match neighbours whose height is responsive, so the icon button
// derives its box from the same source rather than transcribing it.
// The inline-sizing convention in buttonStyles.ts is deliberately relaxed
// here, because the alignment is the whole point.

/** Compact visual controls; coarse-pointer bounds come from index.css. */
export const rowActionBtn =
  'px-3 py-2 sm:py-1.5 text-sm sm:text-xs rounded disabled:opacity-50 inline-flex items-center justify-center text-center'
  + ' transition-colors touch-manipulation whitespace-nowrap max-sm:h-11 min-h-11 sm:min-h-0'
  + ` ${focusRing}`;
export const rowActionIcon =
  'w-11 min-h-11 sm:h-7 sm:w-7 sm:min-h-0 inline-flex items-center justify-center';

/** Table row actions (detection rows, Sponsors, Patterns Protect): 28px on a fine pointer, 44px on coarse through index.css. */
export const tableActionBtn = `h-7 px-1.5 text-xs rounded whitespace-nowrap inline-flex items-center justify-center ${focusRing}`;

/** Review/detection cards: 44px, the tap-target floor. Labels wrap on a phone, where a flex row can squeeze a button below its text. */
const cardActionTail =
  ` px-3 text-sm rounded inline-flex items-center justify-center touch-manipulation sm:whitespace-nowrap text-center max-w-full ${focusRing}`;
export const cardActionBtn = `min-h-[44px]${cardActionTail}`;
/** cardActionBtn with the 44px floor on phones only. */
export const cardActionBtnMobileOnly = `max-sm:min-h-[44px]${cardActionTail}`;

/** Icon button: 44px hit box on coarse pointers; pair with iconVisual for the smaller filled square inside. */
export const iconTarget = `${rowActionIcon} group shrink-0 touch-manipulation focus-visible:outline-hidden`;
/** Filled square inside iconTarget: fills the 44px target on phones and coarse pointers, 28px otherwise; carries the focus ring for the hit box. */
export const iconVisual =
  'h-11 w-11 sm:h-7 sm:w-7 sm:[@media(pointer:coarse)]:h-11 sm:[@media(pointer:coarse)]:w-11 inline-flex items-center justify-center rounded transition-colors'
  + ' group-focus-visible:ring-2 group-focus-visible:ring-ring';

const iconBase = 'inline-flex items-center justify-center';
const iconVisualBase = `${iconBase} rounded transition-colors group-focus-visible:ring-2 group-focus-visible:ring-ring`;
const coarseFill = 'sm:[@media(pointer:coarse)]:h-11 sm:[@media(pointer:coarse)]:w-11';

/**
 * iconTarget and iconVisual sized to the text button beside them: `row` matches rowActionBtn (28 px),
 * `base` matches btnBase (32 px), `field` matches inputBase (40 px), `full` matches cardActionBtn (44 px at every width).
 */
export const iconSizes = {
  row: { target: iconTarget, visual: iconVisual },
  base: {
    target: `${iconBase} w-11 min-h-11 sm:h-8 sm:w-8 sm:min-h-0 group shrink-0 touch-manipulation focus-visible:outline-hidden`,
    visual: `${iconVisualBase} h-11 w-11 sm:h-8 sm:w-8 ${coarseFill}`,
  },
  field: {
    target: `${iconBase} w-11 min-h-11 sm:h-10 sm:w-10 sm:min-h-0 group shrink-0 touch-manipulation focus-visible:outline-hidden`,
    visual: `${iconVisualBase} h-11 w-11 sm:h-10 sm:w-10 ${coarseFill}`,
  },
  full: {
    target: `${iconBase} h-11 w-11 group shrink-0 touch-manipulation focus-visible:outline-hidden`,
    visual: `${iconVisualBase} h-11 w-11`,
  },
} as const;
export type IconSize = keyof typeof iconSizes;
