import { describe, expect, it } from 'vitest';
import { activePricingModelIds, pricingOverrideKey, verificationPricingEnabled } from './modelPricing';

const routes = {
  detection: { provider: 'openai-compatible', model: 'gpt-5' },
  verification: { provider: 'openai-compatible', model: 'gpt-5-mini' },
  verificationEnabled: true,
  reviewer: { enabled: false, provider: 'same_as_pass', model: 'retired-model' },
  chapters: { provider: 'openai-compatible', model: 'chapter-model' },
  cleanup: { provider: 'openai-compatible', model: 'cleanup-model' },
  standby: {
    enabled: false, provider: 'openai-compatible', detectionModel: 'standby-model',
    verificationModel: '', reviewModel: '', chaptersModel: '',
  },
};

describe('active pricing routes', () => {
  it('includes configured manual chapters and cleanup without scheduling flags', () => {
    expect(activePricingModelIds(routes)).toEqual(['gpt-5', 'gpt-5-mini', 'chapter-model', 'cleanup-model']);
  });

  it('omits disabled verification and dormant reviewer selections', () => {
    expect(activePricingModelIds({ ...routes, verificationEnabled: false })).toEqual([
      'gpt-5', 'chapter-model', 'cleanup-model',
    ]);
  });

  it('inherits both pass models while ignoring a dormant explicit reviewer model', () => {
    expect(activePricingModelIds({ ...routes, reviewer: { ...routes.reviewer, enabled: true } })).not.toContain('retired-model');
    expect(activePricingModelIds({
      ...routes, verificationEnabled: false,
      reviewer: { enabled: true, provider: 'primary', model: 'same_as_pass' },
    })).not.toContain('gpt-5-mini');
  });

  it('includes an enabled explicit reviewer and respects an explicitly blank model', () => {
    expect(activePricingModelIds({
      ...routes, reviewer: { enabled: true, provider: 'primary', model: 'review-model' },
    })).toContain('review-model');
    expect(activePricingModelIds({
      ...routes, reviewer: { enabled: true, provider: 'primary', model: '' },
    })).not.toContain('retired-model');
  });

  it('omits unsupported native and proxy chapter or cleanup routes', () => {
    expect(activePricingModelIds({
      ...routes,
      chapters: { provider: 'typesafe', model: 'chapter-model' },
      cleanup: { provider: 'openai-compatible', model: 'typesafe/jev' },
    })).toEqual(['gpt-5', 'gpt-5-mini']);
  });

  it('includes configured standby models before failover activates', () => {
    expect(activePricingModelIds({
      ...routes, reviewer: { ...routes.reviewer, enabled: true },
      standby: { ...routes.standby, enabled: true, verificationModel: 'standby-verification', reviewModel: 'standby-review', chaptersModel: 'standby-chapters' },
    })).toEqual(['gpt-5', 'gpt-5-mini', 'chapter-model', 'cleanup-model', 'standby-model', 'standby-verification', 'standby-review', 'standby-chapters']);
  });

  it('inherits blank standby phase models and excludes disabled or unconfigured standby', () => {
    expect(activePricingModelIds({ ...routes, standby: { ...routes.standby, enabled: true } })).toEqual([
      'gpt-5', 'gpt-5-mini', 'chapter-model', 'cleanup-model', 'standby-model',
    ]);
    expect(activePricingModelIds({ ...routes, standby: { ...routes.standby, enabled: true, detectionModel: '' } })).not.toContain('standby-model');
    expect(activePricingModelIds(routes)).not.toContain('standby-model');
  });

  it('excludes native standby models for every phase and blocked manual routes', () => {
    const ids = activePricingModelIds({
      ...routes, reviewer: { ...routes.reviewer, enabled: true },
      chapters: { provider: 'typesafe', model: 'chapter-model' },
      standby: { ...routes.standby, enabled: true, verificationModel: 'jev-latest', reviewModel: 'typesafe/jev', chaptersModel: 'standby-chapters' },
    });
    expect(ids).toContain('standby-model');
    expect(ids).not.toContain('jev-latest');
    expect(ids).not.toContain('typesafe/jev');
    expect(ids).not.toContain('standby-chapters');
  });

  it('retains distinct exact model IDs even when normalization collides', () => {
    expect(activePricingModelIds({
      ...routes, verification: { provider: 'openai-compatible', model: 'gpt-5:free' },
    })).toContain('gpt-5:free');
  });
});

describe('verification pricing applicability', () => {
  it('includes the globally enabled verification model before any feed exists', () => {
    expect(verificationPricingEnabled(false)).toBe(true);
    expect(verificationPricingEnabled(true)).toBe(false);
  });

  it('includes verification explicitly enabled by a feed while global verification is skipped', () => {
    expect(verificationPricingEnabled(true, [{ processingMode: 'standard', skipSecondPass: false }])).toBe(true);
    expect(verificationPricingEnabled(true, [{ processingMode: 'keep_content', skipSecondPass: false }])).toBe(true);
    expect(verificationPricingEnabled(true, [{ processingMode: 'standard', skipSecondPass: null }])).toBe(false);
  });

  it.each(['passthrough', 'skip_detection', 'cue_only'] as const)('omits verification for %s feeds', (processingMode) => {
    expect(verificationPricingEnabled(true, [{ processingMode, skipSecondPass: false }])).toBe(false);
  });
});

describe('pricing override identity', () => {
  const prices = { inputCostPerMtok: 1, outputCostPerMtok: 2 };

  it('prefers an exact override over normalized aliases with different prices', () => {
    expect(pricingOverrideKey('gpt-5:free', { 'gpt-5:free': { inputCostPerMtok: 0, outputCostPerMtok: 0 }, 'openai/gpt-5': prices })).toBe('gpt-5:free');
  });

  it('uses the unique normalized raw key without changing it', () => {
    expect(pricingOverrideKey('gpt-5', { 'openai/gpt-5-20250929': prices })).toBe('openai/gpt-5-20250929');
  });

  it('does not choose an arbitrary normalized alias when several match', () => {
    expect(pricingOverrideKey('gpt-5', { 'openai/gpt-5': prices, 'gpt-5-20250929': prices })).toBe('gpt-5');
  });
});
