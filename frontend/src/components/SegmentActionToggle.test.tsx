/**
 * SegmentActionToggle only wires the fixed remove/beep/keep action set onto
 * SegmentedToggle; the keyboard contract is tested in SegmentedToggle.test.tsx.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SegmentActionToggle from './SegmentActionToggle';
import { SEGMENT_ACTIONS, SEGMENT_ACTION_LABELS } from '../utils/segmentCategory';

describe('SegmentActionToggle', () => {
  it('renders every segment action, in order, as one named radiogroup', () => {
    render(
      <SegmentActionToggle value="beep" onChange={vi.fn()} ariaLabel="Sponsor action" />,
    );
    const group = screen.getByRole('radiogroup', { name: 'Sponsor action' });
    expect(within(group).getAllByRole('radio').map((el) => el.textContent))
      .toEqual(SEGMENT_ACTIONS.map((action) => SEGMENT_ACTION_LABELS[action]));
    expect(screen.getByRole('radio', { name: 'Beep' }).getAttribute('aria-checked')).toBe('true');
  });

  it('reports the clicked action', async () => {
    const onChange = vi.fn();
    render(
      <SegmentActionToggle value="remove" onChange={onChange} ariaLabel="Sponsor action" />,
    );
    await userEvent.setup().click(screen.getByRole('radio', { name: 'Keep' }));
    expect(onChange).toHaveBeenCalledWith('keep');
  });

  it('renders an inherited value muted', () => {
    render(
      <SegmentActionToggle value="remove" onChange={vi.fn()} ariaLabel="Sponsor action" muted />,
    );
    expect(screen.getByRole('radio', { name: 'Remove' }).className).toContain('bg-muted');
  });
});
