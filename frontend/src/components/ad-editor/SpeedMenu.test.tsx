import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import SpeedMenu from './SpeedMenu';

describe('SpeedMenu', () => {
  it('opens the popover, marks the current rate, and picks a new one', async () => {
    const onChange = vi.fn();
    render(<SpeedMenu playbackRate={1} onChange={onChange} />);
    const user = userEvent.setup();

    const trigger = screen.getByRole('button', { name: 'Playback speed' });
    expect(screen.queryByRole('button', { name: '1.5×' })).toBeNull();

    await user.click(trigger);

    const current = screen.getByRole('button', { name: '1×' });
    expect(current.getAttribute('aria-current')).toBe('true');
    const other = screen.getByRole('button', { name: '1.5×' });
    expect(other.getAttribute('aria-current')).toBe('false');

    await user.click(other);

    expect(onChange).toHaveBeenCalledWith(1.5);
    expect(screen.queryByRole('button', { name: '1.5×' })).toBeNull();
  });
});
