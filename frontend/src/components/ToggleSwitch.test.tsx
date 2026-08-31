/** ToggleSwitch: design-guide sizing (h-5 w-9, 14px knob) and keyboard toggle. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ToggleSwitch from './ToggleSwitch';

describe('ToggleSwitch', () => {
  it('keeps its width beside a long label', () => {
    // A flex item with a width class still shrinks when the row overflows,
    // which only happens at narrow widths. That deformed every switch in the
    // app on mobile while looking correct on desktop.
    render(
      <label className="flex items-center gap-3">
        <ToggleSwitch checked onChange={() => {}} ariaLabel="Toggle" />
        <span>Queue episodes while the LLM or Whisper endpoint is down</span>
      </label>,
    );
    expect(screen.getByRole('switch').className).toContain('shrink-0');
  });

  it('uses the slim design-system track and knob', () => {
    render(<ToggleSwitch checked={false} onChange={() => {}} ariaLabel="Toggle" />);
    const toggle = screen.getByRole('switch', { name: 'Toggle' });
    expect(toggle.className).toContain('h-5');
    expect(toggle.className).toContain('w-9');
    const knob = toggle.querySelector('span');
    expect(knob?.className).toContain('h-3.5');
    expect(knob?.className).toContain('w-3.5');
  });

  it('toggles with click and keyboard', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<ToggleSwitch checked={false} onChange={onChange} ariaLabel="Toggle" />);

    await user.click(screen.getByRole('switch', { name: 'Toggle' }));
    expect(onChange).toHaveBeenLastCalledWith(true);

    rerender(<ToggleSwitch checked={false} onChange={onChange} ariaLabel="Toggle" />);
    await user.keyboard('{ }');
    // Space on the focused switch (click above already fired; focus retained).
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it('marks the checked state for assistive tech', () => {
    render(<ToggleSwitch checked onChange={() => {}} ariaLabel="Toggle" />);
    expect(screen.getByRole('switch', { name: 'Toggle' }).getAttribute('aria-checked')).toBe('true');
  });
});
