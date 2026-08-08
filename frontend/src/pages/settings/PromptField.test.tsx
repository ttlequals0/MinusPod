/**
 * Tests for the per-prompt reset affordance on PromptField (#626): hidden
 * only without a handler, disabled (not hidden) at default, two-click
 * confirm before onReset.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PromptField from './PromptField';

describe('PromptField: per-prompt reset', () => {
  it('renders no reset button when onReset is not supplied', () => {
    render(<PromptField id="p" label="Prompt" value="hello" onChange={() => {}} isDefault={false} />);
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  });

  it('renders the reset button disabled when the prompt is already at its default', () => {
    render(<PromptField id="p" label="Prompt" value="hello" onChange={() => {}} onReset={() => {}} isDefault />);
    const btn = screen.getByRole('button', { name: 'Reset' });
    expect(btn).toHaveProperty('disabled', true);
    expect(btn.getAttribute('title')).toBe('Already the default');
  });

  it('shows the reset button enabled when the prompt has been customized', () => {
    render(<PromptField id="p" label="Prompt" value="hello" onChange={() => {}} onReset={() => {}} isDefault={false} />);
    const btn = screen.getByRole('button', { name: 'Reset' });
    expect(btn).toHaveProperty('disabled', false);
    expect(btn.getAttribute('title')).toBeNull();
  });

  it('requires a second click before firing onReset', async () => {
    const onReset = vi.fn();
    const user = userEvent.setup();
    render(<PromptField id="p" label="Prompt" value="hello" onChange={() => {}} onReset={onReset} isDefault={false} />);
    const btn = screen.getByRole('button', { name: 'Reset' });
    await user.click(btn);
    expect(onReset).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Click again to confirm' })).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});
