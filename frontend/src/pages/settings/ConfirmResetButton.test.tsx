import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConfirmResetButton from './ConfirmResetButton';

describe('ConfirmResetButton', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('requires a second click before firing onConfirm', () => {
    const onConfirm = vi.fn();
    render(<ConfirmResetButton label="Reset Prompts to Default" isPending={false} onConfirm={onConfirm} />);
    const btn = screen.getByRole('button', { name: 'Reset Prompts to Default' });
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(btn.textContent).toBe('Click again to confirm');
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(btn.textContent).toBe('Reset Prompts to Default');
  });

  it('disarms after 3 seconds without a second click', () => {
    const onConfirm = vi.fn();
    render(<ConfirmResetButton label="Reset" isPending={false} onConfirm={onConfirm} />);
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(btn.textContent).toBe('Click again to confirm');
    act(() => {
      vi.advanceTimersByTime(3100);
    });
    expect(btn.textContent).toBe('Reset');
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('shows the pending state and disables the button', () => {
    render(<ConfirmResetButton label="Reset" isPending onConfirm={() => {}} />);
    const btn = screen.getByRole('button', { name: 'Resetting...' });
    expect(btn).toHaveProperty('disabled', true);
  });
});
