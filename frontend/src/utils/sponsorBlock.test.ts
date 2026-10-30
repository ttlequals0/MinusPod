import { describe, it, expect } from 'vitest';
import { sponsorBlockLabel } from './sponsorBlock';

const now = Date.parse('2026-10-29T12:00:00Z');
const base = { attempts: 0, nextCheckAt: null, segmentCount: 0, note: null, lastError: null };

describe('sponsorBlockLabel', () => {
  it('describes each state', () => {
    expect(sponsorBlockLabel({ ...base, state: 'probing', attempts: 2, nextCheckAt: '2026-10-29T12:25:00Z' }, 6, now))
      .toBe('Waiting for SponsorBlock, attempt 2 of 6, next check in 25 min');
    expect(sponsorBlockLabel({ ...base, state: 'watching', attempts: 6, nextCheckAt: '2026-10-29T18:00:00Z' }, 6, now))
      .toBe('Watching SponsorBlock, next check in 6 h');
    expect(sponsorBlockLabel({ ...base, state: 'done', attempts: 1, segmentCount: 4 }, 6, now))
      .toBe('SponsorBlock: 4 segments');
    expect(sponsorBlockLabel({ ...base, state: 'exhausted', attempts: 6 }, 6, now))
      .toBe('SponsorBlock timed out, processed with LLM');
    expect(sponsorBlockLabel({ ...base, state: 'llm' }, 6, now))
      .toBe('No SponsorBlock marks, processed with LLM');
    expect(sponsorBlockLabel({ ...base, state: 'done', attempts: 1, segmentCount: 1, note: 'full' }, 6, now))
      .toBe('SponsorBlock: 1 segment, marked whole video as sponsored');
    expect(sponsorBlockLabel({ ...base, state: null }, 6, now)).toBeNull();
    expect(sponsorBlockLabel(null, 6, now)).toBeNull();
  });

  it('omits the maximum without probeAttempts and formats relative times', () => {
    expect(sponsorBlockLabel({ ...base, state: 'waiting', attempts: 1, nextCheckAt: '2026-10-29T11:00:00Z' }, undefined, now))
      .toBe('Waiting for SponsorBlock, attempt 1, next check in now');
    expect(sponsorBlockLabel({ ...base, state: 'watching', nextCheckAt: '2026-10-31T12:00:00Z' }, undefined, now))
      .toBe('Watching SponsorBlock, next check in 2 days');
  });

  it('says queued when a waiting or probing row has no next check', () => {
    expect(sponsorBlockLabel({ ...base, state: 'probing', attempts: 2 }, 6, now))
      .toBe('Waiting for SponsorBlock, queued');
    expect(sponsorBlockLabel({ ...base, state: 'waiting' }, 6, now))
      .toBe('Waiting for SponsorBlock, queued');
  });

  it('appends the no-captions suffix and keeps the full note', () => {
    expect(sponsorBlockLabel({ ...base, state: 'done', attempts: 1, segmentCount: 2, note: 'no_captions' }, 6, now))
      .toBe('SponsorBlock: 2 segments, no captions (transcription off)');
    expect(sponsorBlockLabel({ ...base, state: 'done', attempts: 1, segmentCount: 1, note: 'full,no_captions' }, 6, now))
      .toBe('SponsorBlock: 1 segment, marked whole video as sponsored, no captions (transcription off)');
  });

  it('labels an exhausted no-caption episode as served unmodified', () => {
    expect(sponsorBlockLabel({ ...base, state: 'exhausted', attempts: 3, note: 'no_captions' }, 6, now))
      .toBe('No marks and no captions, served unmodified');
  });

  it('appends the last-check failure', () => {
    expect(sponsorBlockLabel({ ...base, state: 'watching', nextCheckAt: '2026-10-29T12:30:00Z', lastError: 'boom' }, 6, now))
      .toBe('Watching SponsorBlock, next check in 30 min, last check failed');
  });

  it('returns null for an unknown state', () => {
    expect(sponsorBlockLabel({ ...base, state: 'bogus' as never, lastError: 'x' }, 6, now)).toBeNull();
  });
});
