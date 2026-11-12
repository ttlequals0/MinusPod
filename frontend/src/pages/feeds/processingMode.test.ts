import { describe, it, expect } from 'vitest';
import { PROCESSING_MODE_HINTS, processingModeHint } from './processingMode';

describe('processingModeHint', () => {
  it('leaves every hint alone when a provider is set', () => {
    expect(processingModeHint('standard', true)).toBe(PROCESSING_MODE_HINTS.standard);
    expect(processingModeHint('keep_content', true)).toBe(PROCESSING_MODE_HINTS.keep_content);
  });

  it('describes model-free detection for Standard under None without repeating the reason', () => {
    const hint = processingModeHint('standard', false);
    expect(hint.text).toBe(
      'Detects ads with text patterns, audio fingerprints, audio cues and cross-fetch comparison, and cuts them out. The default for most feeds.',
    );
    expect(hint.text).not.toContain('LLM provider');
  });

  it('says keep content runs as Standard under None, without a warning', () => {
    expect(processingModeHint('keep_content', false)).toEqual({
      text: 'Keep content runs as Standard while the LLM provider is set to None.',
      warning: false,
    });
  });
});
