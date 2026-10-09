import { describe, expect, it } from 'vitest';
import { effectiveReviewModels, effectiveStageModel, isSystemOneChapterRoute, isSystemOneRoute, systemOneRouteLabel } from './systemoneWarnings';
import type { Settings } from '../../api/types';

describe('System One route warnings', () => {
  it('resolves chapter inheritance and falls back from an unusable secondary slot', () => {
    const settings = {
      llmProvider: { value: 'anthropic' }, claudeModel: { value: 'claude-sonnet' },
      detectionProvider: { value: 'secondary' }, chaptersProvider: { value: 'same_as_detection' },
      secondaryProviderEnabled: { value: true }, secondaryProvider: { value: 'typesafe' },
      chaptersModel: { value: null },
    } as unknown as Settings;
    expect(isSystemOneChapterRoute(settings)).toBe(true);
    settings.secondaryProviderEnabled.value = false;
    expect(isSystemOneChapterRoute(settings)).toBe(false);
    settings.secondaryProviderEnabled.value = true;
    settings.secondaryProvider.value = '';
    expect(isSystemOneChapterRoute(settings)).toBe(false);
  });
  it('recognizes native provider routes and the explicit proxy model IDs', () => {
    expect(isSystemOneRoute('typesafe', 'any-model')).toBe(true);
    expect(isSystemOneRoute('systemone-compatible', 'proxy-model')).toBe(true);
    expect(isSystemOneRoute('openai-compatible', 'jev-latest')).toBe(true);
    expect(isSystemOneRoute('openai-compatible', 'custom-jev-model')).toBe(false);
  });

  it('keeps the configured provider and model visible in warnings', () => {
    expect(systemOneRouteLabel('typesafe', 'jev-latest')).toBe('TypeSafe / jev-latest');
  });

  it('uses both pass models when review provider inherits, even with a dormant model', () => {
    expect(effectiveReviewModels('same_as_pass', 'old-review-model', ['detection-model', 'verification-model']))
      .toEqual(['detection-model', 'verification-model']);
  });

  it('keeps an explicitly blank review model unconfigured on an explicit provider', () => {
    expect(effectiveReviewModels('primary', '', ['detection-model', 'verification-model']))
      .toEqual(['', '']);
  });

  it('inherits a pass model only when the saved stage model is null', () => {
    expect(effectiveStageModel('detection-model', null, '')).toBe('detection-model');
    expect(effectiveStageModel('detection-model', '', '')).toBe('');
    expect(effectiveStageModel('detection-model', 'stage-model', 'stage-model')).toBe('stage-model');
    expect(effectiveStageModel('detection-model', null, 'new-stage-model')).toBe('new-stage-model');
  });
});
