import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LLM_DISABLED_REASON, LlmGate, isLlmDisabledError } from './LlmGate';
import { ApiError } from '../../api/client';

describe('LlmGate', () => {
  it('renders the reason once and ties it to the disabled fieldset', () => {
    render(<LlmGate disabled id="demo"><label>Name<input /></label></LlmGate>);
    expect(screen.getAllByText(LLM_DISABLED_REASON)).toHaveLength(1);
    const group = screen.getByRole('group');
    expect((group as HTMLFieldSetElement).disabled).toBe(true);
    expect(group.getAttribute('aria-describedby')).toBe('demo-llm-disabled');
  });

  it('renders no reason and no description when enabled', () => {
    render(<LlmGate disabled={false} id="demo"><label>Name<input /></label></LlmGate>);
    expect(screen.queryByText(LLM_DISABLED_REASON)).toBeNull();
    const group = screen.getByRole('group');
    expect((group as HTMLFieldSetElement).disabled).toBe(false);
    expect(group.hasAttribute('aria-describedby')).toBe(false);
  });
});

describe('isLlmDisabledError', () => {
  it('matches only a 409 carrying the llm_disabled code', () => {
    expect(isLlmDisabledError(new ApiError('x', 409, { code: 'llm_disabled' }))).toBe(true);
    expect(isLlmDisabledError(new ApiError('x', 409, { code: 'cleanup_in_progress' }))).toBe(false);
    expect(isLlmDisabledError(new ApiError('x', 400, { code: 'llm_disabled' }))).toBe(false);
    expect(isLlmDisabledError(new Error('x'))).toBe(false);
  });
});
