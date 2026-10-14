// First product token of a user agent ("Overcast/3.0 (iPhone)" -> "Overcast").
export function clientName(userAgent: string): string {
  const token = userAgent.split(/[/(]/)[0].trim();
  return token || 'Unknown';
}

// Anchored on the product token so a block survives app version bumps.
export function blockPatternFor(userAgent: string): string {
  return `^${clientName(userAgent)}`;
}
