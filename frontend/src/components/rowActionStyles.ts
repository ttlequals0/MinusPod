import { focusRing } from './fieldStyles';

// Sizing for the action buttons in a marker/detection row and the icon button beside them. Paired on purpose:
// a fixed icon size cannot match neighbours of responsive height, so the icon button derives its box from the
// same source. The inline-sizing convention in buttonStyles.ts is relaxed here because alignment is the point.

/** Compact visual controls; coarse-pointer bounds come from index.css. */
export const rowActionBtn =
  'px-3 py-2 sm:py-1.5 text-sm sm:text-xs rounded disabled:opacity-50 inline-flex items-center justify-center gap-1.5 text-center'
  + ' transition-colors touch-manipulation whitespace-nowrap max-sm:h-11 min-h-11 sm:min-h-0'
  + ` ${focusRing}`;
/** Table row actions (detection rows, Sponsors, Patterns Protect): 28px on a fine pointer, 44px on coarse through index.css. */
export const tableActionBtn = `h-7 px-1.5 text-xs rounded whitespace-nowrap inline-flex items-center justify-center ${focusRing}`;

/** Review/detection cards: 44px, the tap-target floor. Labels wrap on a phone, where a flex row can squeeze a button below its text. */
const cardActionTail =
  ` px-3 text-sm rounded inline-flex items-center justify-center touch-manipulation sm:whitespace-nowrap text-center max-w-full ${focusRing}`;
export const cardActionBtn = `min-h-[44px]${cardActionTail}`;
/** cardActionBtn with the 44px floor on phones only. */
export const cardActionBtnMobileOnly = `max-sm:min-h-[44px]${cardActionTail}`;

const iconBase = 'inline-flex items-center justify-center';
const iconTargetTail = 'group shrink-0 touch-manipulation focus-visible:outline-hidden';
const iconVisualBase = `${iconBase} rounded transition-colors group-focus-visible:ring-2 group-focus-visible:ring-ring`;
const coarseFill = 'sm:[@media(pointer:coarse)]:h-11 sm:[@media(pointer:coarse)]:w-11';
// Tailwind reads class names from source, so the fine-pointer size per px value is spelled out once here.
const FINE_SIZE = { 28: 'sm:h-7 sm:w-7', 32: 'sm:h-8 sm:w-8', 40: 'sm:h-10 sm:w-10' } as const;

// 44 px below sm and on coarse pointers; `px` is the size from sm up on a fine pointer.
function iconSize(px: keyof typeof FINE_SIZE | 44) {
  if (px === 44) {
    return { target: `${iconBase} h-11 w-11 ${iconTargetTail}`, visual: `${iconVisualBase} h-11 w-11` };
  }
  const fine = FINE_SIZE[px];
  return {
    target: `${iconBase} w-11 min-h-11 sm:min-h-0 ${fine} ${iconTargetTail}`,
    visual: `${iconVisualBase} h-11 w-11 ${fine} ${coarseFill}`,
  };
}

/** iconTarget and iconVisual sized to the text button beside them: row 28 px, base 32 px, field 40 px, full 44 px. */
export const iconSizes = {
  row: iconSize(28),
  base: iconSize(32),
  field: iconSize(40),
  full: iconSize(44),
} as const;
export type IconSize = keyof typeof iconSizes;

/** Icon button: 44px hit box on coarse pointers; pair with iconVisual for the smaller filled square inside (28px from sm on a fine pointer). */
export const iconTarget = iconSizes.row.target;
/** Filled square inside iconTarget: fills the 44px target on phones and coarse pointers, 28px otherwise; carries the focus ring for the hit box. */
export const iconVisual = iconSizes.row.visual;
