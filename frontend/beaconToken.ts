// Accepts a bare token or a pasted Web Analytics snippet; prefers the "token" value in data-cf-beacon.
export function beaconToken(raw: string | undefined): string | null {
  const text = raw?.trim() ?? '';
  const keyed = text.match(/(?:"|&quot;|')token(?:"|&quot;|')\s*:\s*(?:"|&quot;|')([0-9a-f]{32})(?:"|&quot;|')/i);
  if (keyed) return keyed[1];
  return /^[0-9a-f]{32}$/i.test(text) ? text : null;
}
