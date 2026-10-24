import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConfirmResetButton from './ConfirmResetButton';

describe('ConfirmResetButton', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('requires explicit confirmation before firing onConfirm', () => {
    const onConfirm = vi.fn();
    render(<ConfirmResetButton label="Reset prompts to default" isPending={false} onConfirm={onConfirm} />);
    const btn = screen.getByRole('button', { name: 'Reset prompts to default' });
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reset prompts to default' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(btn.textContent).toBe('Reset prompts to default');
  });

  it('keeps the confirmation open until explicitly cancelled', () => {
    const onConfirm = vi.fn();
    render(<ConfirmResetButton label="Reset" isPending={false} onConfirm={onConfirm} />);
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(screen.getByRole('dialog')).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(3100);
    });
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('shows the pending state and disables the button', () => {
    render(<ConfirmResetButton label="Reset" isPending onConfirm={() => {}} />);
    const btn = screen.getByRole('button', { name: 'Resetting...' });
    expect(btn).toHaveProperty('disabled', true);
  });

  it('disables via the disabled prop without switching to the pending label', () => {
    const onConfirm = vi.fn();
    render(<ConfirmResetButton label="Reset" disabled title="Already the default" onConfirm={onConfirm} />);
    const btn = screen.getByRole('button', { name: 'Reset' });
    expect(btn).toHaveProperty('disabled', true);
    expect(btn.getAttribute('title')).toBe('Already the default');
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('shows the consequence as visible text only while armed', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmResetButton
        label="Reset all"
        isPending={false}
        onConfirm={onConfirm}
        confirmHint="Also clears your AI model choices; you may need to pick them again."
      />
    );
    const hint = 'Also clears your AI model choices; you may need to pick them again.';
    expect(screen.queryByText(hint)).toBeNull();
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText(hint)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(3100);
    });
    expect(screen.getByText(hint)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText(hint)).toBeNull();
  });
});
