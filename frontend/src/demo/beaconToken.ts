// Accepts a bare token or a pasted Web Analytics snippet; returns the first 32-hex run.
export function beaconToken(raw: string | undefined): string | null {
  return raw?.match(/[0-9a-f]{32}/i)?.[0] ?? null;
}
