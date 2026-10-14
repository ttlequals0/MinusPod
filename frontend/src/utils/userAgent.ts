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

// Mirrors user_agent_is_jit_blocked in src/config.py.
export function userAgentIsBlocked(userAgent: string, patterns: string[]): boolean {
  const low = userAgent.toLowerCase();
  return patterns.some((p) => {
    const pat = p.toLowerCase();
    return pat.startsWith('^') ? low.startsWith(pat.slice(1)) : low.includes(pat);
  });
}
