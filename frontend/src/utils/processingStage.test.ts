import { describe, it, expect } from 'vitest';
import { isActionBlocked } from './processingStage';

describe('isActionBlocked', () => {
  it('blocks while queued, processing, or the client-side submitting state', () => {
    expect(isActionBlocked('queued', false)).toBe(true);
    expect(isActionBlocked('processing', false)).toBe(true);
    expect(isActionBlocked('submitting', false)).toBe(true);
  });

  it('blocks while a request is in flight regardless of jobState', () => {
    expect(isActionBlocked('idle', true)).toBe(true);
    expect(isActionBlocked(undefined, true)).toBe(true);
  });

  it('allows the action when idle and not submitting', () => {
    expect(isActionBlocked('idle', false)).toBe(false);
    expect(isActionBlocked(undefined, false)).toBe(false);
  });
});
