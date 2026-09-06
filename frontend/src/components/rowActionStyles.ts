// Sizing for the action buttons in a marker/detection row, and for the icon
// button that sits beside them. These are paired on purpose: a fixed icon
// size cannot match neighbours whose height is responsive, so the icon button
// derives its box from the same source rather than transcribing it.
// The inline-sizing convention in buttonStyles.ts is deliberately relaxed
// here, because the alignment is the whole point.

/** Episode marker rows: 40px tall on mobile, 28px from sm: up. */
export const rowActionBtn =
  'px-3 py-2 sm:py-1.5 text-sm sm:text-xs rounded disabled:opacity-50'
  + ' transition-colors touch-manipulation min-h-[40px] sm:min-h-0';
export const rowActionIcon =
  'w-10 min-h-[40px] sm:h-7 sm:w-7 sm:min-h-0 inline-flex items-center justify-center';

/** Desktop detection table rows: 30px tall. */
export const tableActionBtn = 'px-1.5 py-1 text-xs rounded whitespace-nowrap';
export const tableActionIcon =
  'h-[30px] w-[30px] inline-flex items-center justify-center';

/** Review/detection cards: 44px, the tap-target floor. */
export const cardActionBtn =
  'min-h-[44px] px-3 text-sm rounded inline-flex items-center'
  + ' justify-center touch-manipulation whitespace-nowrap text-center max-w-full';
export const cardActionIcon =
  'h-11 w-11 shrink-0 inline-flex items-center justify-center';
