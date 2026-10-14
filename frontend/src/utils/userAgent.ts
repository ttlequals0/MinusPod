const FULL_PATTERN_MAX = 40;

// First product token of a user agent ("Overcast/3.0 (iPhone)" -> "Overcast").
export function clientName(userAgent: string): string {
  const token = userAgent.split(/[/(]/)[0].trim();
  return token || 'Unknown';
}

// Short agents are anchored whole; long ones by their first product token.
export function blockPatternFor(userAgent: string): string {
  const ua = userAgent.trim();
  return `^${ua.length < FULL_PATTERN_MAX ? ua : clientName(ua)}`;
}
