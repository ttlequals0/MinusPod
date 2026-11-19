import type { RatingValue } from '../api/types';

// ?rate= accepts up, down and the legacy 1-5 stars (5 is up, 1-4 down).
export function parseRateParam(raw: string | null | undefined): RatingValue | undefined {
  if (raw === 'up' || raw === 'down') return raw;
  const n = Number(raw);
  if (!raw || !Number.isInteger(n) || n < 1 || n > 5) return undefined;
  return n === 5 ? 'up' : 'down';
}
