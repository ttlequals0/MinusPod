import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import CloseButton from './CloseButton';

describe('CloseButton', () => {
  it('has a default accessible name and fires onClick', async () => {
    const onClick = vi.fn();
    render(<CloseButton onClick={onClick} />);
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('uses a custom label and className', () => {
    render(<CloseButton onClick={() => {}} label="Dismiss" className="ml-auto" />);
    const btn = screen.getByRole('button', { name: 'Dismiss' });
    expect(btn.getAttribute('type')).toBe('button');
    expect(btn.classList.contains('ml-auto')).toBe(true);
  });
});
