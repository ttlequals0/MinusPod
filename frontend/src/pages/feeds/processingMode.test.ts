import { describe, it, expect } from 'vitest';
import { PROCESSING_MODE_HINTS, processingModeHint } from './processingMode';

describe('processingModeHint', () => {
  it('leaves every hint alone when a provider is set', () => {
    expect(processingModeHint('standard', true)).toBe(PROCESSING_MODE_HINTS.standard);
    expect(processingModeHint('keep_content', true)).toBe(PROCESSING_MODE_HINTS.keep_content);
  });

  it('adds the model-free detectors line to Standard under None', () => {
    const hint = processingModeHint('standard', false);
    expect(hint.text.startsWith(PROCESSING_MODE_HINTS.standard.text)).toBe(true);
    expect(hint.text).toContain(
      'LLM provider is set to None, so detection uses text patterns, audio fingerprints, audio cues and cross-fetch comparison.',
    );
  });

  it('says keep content runs as Standard under None, without a warning', () => {
    expect(processingModeHint('keep_content', false)).toEqual({
      text: 'Keep content runs as Standard while the LLM provider is set to None.',
      warning: false,
    });
  });
});
