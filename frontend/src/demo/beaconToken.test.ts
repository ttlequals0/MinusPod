import { describe, expect, it } from 'vitest';
import { beaconToken } from './beaconToken';

const TOKEN = '0123456789abcdef0123456789ABCDEF';

describe('beaconToken', () => {
  it('returns a bare token', () => {
    expect(beaconToken(TOKEN)).toBe(TOKEN);
  });

  it('extracts the token from a pasted snippet', () => {
    const snippet = `<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token": "${TOKEN}"}'></script>`;
    expect(beaconToken(snippet)).toBe(TOKEN);
  });

  it('returns null when nothing matches', () => {
    expect(beaconToken(undefined)).toBeNull();
    expect(beaconToken('')).toBeNull();
    expect(beaconToken('not-a-token')).toBeNull();
  });
});
