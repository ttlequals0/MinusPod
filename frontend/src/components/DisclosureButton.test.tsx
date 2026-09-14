import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DisclosureButton from './DisclosureButton';

describe('DisclosureButton', () => {
  it('keeps a 44px tap target on mobile without growing the icon', () => {
    render(<DisclosureButton expanded={false} onToggle={vi.fn()} label="Show detail" />);
    const button = screen.getByRole('button', { name: 'Show detail' });
    expect(button.className).toMatch(/min-h-11/);
    expect(button.className).toMatch(/min-w-11/);
    expect(button.querySelector('svg')?.getAttribute('width')).toBe('14');
  });

  it('reports and toggles expansion from the keyboard', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<DisclosureButton expanded onToggle={onToggle} label="Hide detail" />);
    const button = screen.getByRole('button', { name: 'Hide detail' });
    expect(button.getAttribute('aria-expanded')).toBe('true');

    button.focus();
    await user.keyboard('{Enter}');
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('shows the label as text rather than an aria-label when asked', () => {
    render(<DisclosureButton expanded={false} onToggle={vi.fn()} label="Show usage detail" showLabel />);
    const button = screen.getByRole('button', { name: 'Show usage detail' });
    expect(button.getAttribute('aria-label')).toBeNull();
  });
});
