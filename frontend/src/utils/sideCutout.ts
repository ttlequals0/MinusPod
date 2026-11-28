const KEY = 'sideCutout';
const FILL = 'fill';

export function readSideCutoutFill(): boolean {
  try {
    return localStorage.getItem(KEY) === FILL;
  } catch {
    return false;
  }
}

// Sets the root attribute and persists the choice; storage failures are ignored.
export function setSideCutoutFill(on: boolean): void {
  const root = document.documentElement;
  if (on) root.setAttribute('data-side-cutout', FILL);
  else root.removeAttribute('data-side-cutout');
  try {
    if (on) localStorage.setItem(KEY, FILL);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the choice lasts for this session only */
  }
}

// Applies the stored choice before first render so the layout does not jump.
export function applyStoredSideCutout(): void {
  if (readSideCutoutFill()) document.documentElement.setAttribute('data-side-cutout', FILL);
}

// Largest side safe-area inset in CSS px, read through a hidden probe.
export function measureSideInset(): number {
  const probe = document.createElement('div');
  probe.setAttribute('aria-hidden', 'true');
  probe.setAttribute('data-side-inset-probe', '');
  probe.style.cssText = 'position:fixed;top:0;left:0;width:0;height:0;visibility:hidden;pointer-events:none;padding-left:env(safe-area-inset-left);padding-right:env(safe-area-inset-right)';
  document.body.appendChild(probe);
  const cs = getComputedStyle(probe);
  const inset = Math.max(parseFloat(cs.paddingLeft) || 0, parseFloat(cs.paddingRight) || 0);
  probe.remove();
  return inset;
}
