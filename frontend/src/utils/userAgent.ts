// First product token of a user agent ("Overcast/3.0 (iPhone)" -> "Overcast").
function productToken(userAgent: string): string {
  return userAgent.split(/[/(]/)[0].trim();
}

export function clientName(userAgent: string): string {
  return productToken(userAgent) || 'Unknown';
}

// Anchored on the product token so a block survives app version bumps.
export function blockPatternFor(userAgent: string): string | null {
  const token = productToken(userAgent);
  return token ? `^${token}` : null;
}
