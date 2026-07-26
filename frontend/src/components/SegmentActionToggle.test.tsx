/**
 * Tests SegmentActionToggle's ARIA radiogroup keyboard pattern (issue #565):
 * roving tabindex, Left/Up, Right/Down, Home, End moving and committing
 * the selection.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SegmentActionToggle from './SegmentActionToggle';
import type { SegmentAction } from '../utils/segmentCategory';

function Harness({ initial = 'remove' as SegmentAction, onChange }: {
  initial?: SegmentAction;
  onChange?: (action: SegmentAction) => void;
}) {
  const [value, setValue] = useState<SegmentAction>(initial);
  return (
    <SegmentActionToggle
      value={value}
      onChange={(action) => {
        setValue(action);
        onChange?.(action);
      }}
      ariaLabel="Sponsor action"
    />
  );
}

describe('SegmentActionToggle: roving tabindex', () => {
  it('only the selected option is a tab stop', () => {
    render(<Harness initial="beep" />);
    expect(screen.getByRole('radio', { name: 'Remove' }).getAttribute('tabindex')).toBe('-1');
    expect(screen.getByRole('radio', { name: 'Beep' }).getAttribute('tabindex')).toBe('0');
    expect(screen.getByRole('radio', { name: 'Keep' }).getAttribute('tabindex')).toBe('-1');
  });
});

describe('SegmentActionToggle: arrow key navigation', () => {
  it('ArrowRight moves selection to the next option and fires onChange', async () => {
    const user = userEvent.setup();
    const changes: SegmentAction[] = [];
    render(<Harness initial="remove" onChange={(a) => changes.push(a)} />);

    screen.getByRole('radio', { name: 'Remove' }).focus();
    await user.keyboard('{ArrowRight}');

    expect(changes).toEqual(['beep']);
    expect(screen.getByRole('radio', { name: 'Beep' }).getAttribute('aria-checked')).toBe('true');
    expect(document.activeElement).toBe(screen.getByRole('radio', { name: 'Beep' }));
  });

  it('ArrowDown behaves the same as ArrowRight', async () => {
    const user = userEvent.setup();
    const changes: SegmentAction[] = [];
    render(<Harness initial="remove" onChange={(a) => changes.push(a)} />);

    screen.getByRole('radio', { name: 'Remove' }).focus();
    await user.keyboard('{ArrowDown}');

    expect(changes).toEqual(['beep']);
  });

  it('ArrowRight wraps from the last option back to the first', async () => {
    const user = userEvent.setup();
    const changes: SegmentAction[] = [];
    render(<Harness initial="keep" onChange={(a) => changes.push(a)} />);

    screen.getByRole('radio', { name: 'Keep' }).focus();
    await user.keyboard('{ArrowRight}');

    expect(changes).toEqual(['remove']);
    expect(document.activeElement).toBe(screen.getByRole('radio', { name: 'Remove' }));
  });

  it('ArrowLeft wraps from the first option back to the last', async () => {
    const user = userEvent.setup();
    const changes: SegmentAction[] = [];
    render(<Harness initial="remove" onChange={(a) => changes.push(a)} />);

    screen.getByRole('radio', { name: 'Remove' }).focus();
    await user.keyboard('{ArrowLeft}');

    expect(changes).toEqual(['keep']);
    expect(document.activeElement).toBe(screen.getByRole('radio', { name: 'Keep' }));
  });

  it('ArrowUp behaves the same as ArrowLeft', async () => {
    const user = userEvent.setup();
    const changes: SegmentAction[] = [];
    render(<Harness initial="beep" onChange={(a) => changes.push(a)} />);

    screen.getByRole('radio', { name: 'Beep' }).focus();
    await user.keyboard('{ArrowUp}');

    expect(changes).toEqual(['remove']);
  });

  it('Home selects the first option and End selects the last', async () => {
    const user = userEvent.setup();
    const changes: SegmentAction[] = [];
    render(<Harness initial="beep" onChange={(a) => changes.push(a)} />);

    screen.getByRole('radio', { name: 'Beep' }).focus();
    await user.keyboard('{End}');
    expect(changes).toEqual(['keep']);

    screen.getByRole('radio', { name: 'Keep' }).focus();
    await user.keyboard('{Home}');
    expect(changes).toEqual(['keep', 'remove']);
  });
});
