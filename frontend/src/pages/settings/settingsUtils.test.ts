import { describe, it, expect } from 'vitest';
import { reconcileStageSlotsForSecondaryToggle, type StageProviderSlots } from './settingsUtils';

function slots(overrides: Partial<StageProviderSlots> = {}): StageProviderSlots {
  return {
    detectionProvider: 'primary',
    verificationProvider: 'same_as_detection',
    chaptersProvider: 'same_as_detection',
    reviewProvider: 'same_as_pass',
    ...overrides,
  };
}

describe('reconcileStageSlotsForSecondaryToggle', () => {
  it('leaves every stage untouched when enabling', () => {
    const current = slots({ detectionProvider: 'secondary', reviewProvider: 'secondary' });
    expect(reconcileStageSlotsForSecondaryToggle(true, current)).toEqual(current);
  });

  it('leaves stages that are not on secondary untouched when disabling', () => {
    const current = slots();
    expect(reconcileStageSlotsForSecondaryToggle(false, current)).toEqual(current);
  });

  it('reverts every stage pointed at secondary to primary when disabling', () => {
    const current = slots({
      detectionProvider: 'secondary',
      verificationProvider: 'secondary',
      chaptersProvider: 'secondary',
      reviewProvider: 'secondary',
    });
    expect(reconcileStageSlotsForSecondaryToggle(false, current)).toEqual({
      detectionProvider: 'primary',
      verificationProvider: 'primary',
      chaptersProvider: 'primary',
      reviewProvider: 'primary',
    });
  });

  it('reverts only the stages actually on secondary, leaving the rest as-is', () => {
    const current = slots({ detectionProvider: 'secondary', chaptersProvider: 'same_as_detection' });
    expect(reconcileStageSlotsForSecondaryToggle(false, current)).toEqual({
      detectionProvider: 'primary',
      verificationProvider: 'same_as_detection',
      chaptersProvider: 'same_as_detection',
      reviewProvider: 'same_as_pass',
    });
  });
});

