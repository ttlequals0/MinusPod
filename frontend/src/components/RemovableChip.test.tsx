import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RemovableChip } from './RemovableChip';

describe('RemovableChip', () => {
  it('renders the label and removes on click', async () => {
    const onRemove = vi.fn();
    render(<RemovableChip label="atc/" onRemove={onRemove} />);
    expect(screen.getByText('atc/')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Remove atc/' }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('cannot be removed while a save is in flight', () => {
    const onRemove = vi.fn();
    render(<RemovableChip label="atc/" onRemove={onRemove} disabled />);
    const button = screen.getByRole('button', { name: 'Remove atc/' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    // fireEvent dispatches even on a disabled button, so the handler staying
    // unreached is the component's doing, not the click helper's guard.
    fireEvent.click(button);
    expect(onRemove).not.toHaveBeenCalled();
  });
});
