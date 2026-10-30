import { describe, it, expect } from 'vitest';
import { reprocessMenuCopy } from './reprocessMenu';

describe('reprocessMenuCopy', () => {
  it('describes the YouTube pipeline for youtube feeds', () => {
    const copy = reprocessMenuCopy('youtube');
    expect(copy.reprocess.subtitle).toBe('SponsorBlock marks + patterns');
    expect(copy.full.subtitle).toBe('Marks, patterns + pass 1 LLM');
    expect(copy.redetect.subtitle).toBe('Keep transcript, add pass 1 LLM');
  });

  it.each([undefined, 'subscribed', 'local'])('keeps the pattern + AI wording for %s', (feedType) => {
    const copy = reprocessMenuCopy(feedType);
    expect(copy.reprocess.subtitle).toBe('Use patterns + AI');
    expect(copy.full.subtitle).toBe('Skip patterns, AI only');
    expect(copy.redetect.subtitle).toBe('Keep transcript, re-cut');
  });
});
