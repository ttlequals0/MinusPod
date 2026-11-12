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

  it('describes model-free detection when the LLM provider is None', () => {
    const copy = reprocessMenuCopy(undefined, false);
    expect(copy.reprocess.subtitle).toBe('Use patterns and the other model-free detectors');
    expect(copy.full.subtitle).toBe('Same as Reprocess while the LLM provider is set to None');
    expect(copy.full.tooltip).not.toMatch(/AI/);
    expect(copy.redetect.subtitle).toBe('Keep transcript, re-cut');
  });

  it('drops the pass 1 LLM wording for youtube feeds under None', () => {
    const copy = reprocessMenuCopy('youtube', false);
    expect(JSON.stringify(copy)).not.toMatch(/pass 1 LLM/);
    expect(copy.full.subtitle).toBe('Same as Reprocess while the LLM provider is set to None');
  });
});
