import { describe, it, expect } from 'vitest';
import { reconcileStageSlotsForSecondaryToggle, splitSecondaryProviderPayload, type StageProviderSlots } from './settingsUtils';

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

describe('splitSecondaryProviderPayload', () => {
  it('separates the secondary provider keys from everything else', () => {
    const { secondaryPayload, restPayload } = splitSecondaryProviderPayload({
      llmProvider: 'openai-compatible',
      openaiBaseUrl: 'http://localhost:8000/v1',
      secondaryProviderEnabled: true,
      secondaryProvider: 'anthropic',
      secondaryProviderBaseUrl: '',
    });
    expect(secondaryPayload).toEqual({
      secondaryProviderEnabled: true,
      secondaryProvider: 'anthropic',
      secondaryProviderBaseUrl: '',
    });
    expect(restPayload).toEqual({
      llmProvider: 'openai-compatible',
      openaiBaseUrl: 'http://localhost:8000/v1',
    });
  });

  it('returns an empty secondaryPayload when no secondary field changed', () => {
    const { secondaryPayload, restPayload } = splitSecondaryProviderPayload({
      llmProvider: 'anthropic',
    });
    expect(secondaryPayload).toEqual({});
    expect(restPayload).toEqual({ llmProvider: 'anthropic' });
  });

  it('returns an empty restPayload when only secondary fields changed', () => {
    const { secondaryPayload, restPayload } = splitSecondaryProviderPayload({
      secondaryProviderEnabled: true,
    });
    expect(secondaryPayload).toEqual({ secondaryProviderEnabled: true });
    expect(restPayload).toEqual({});
  });
});
