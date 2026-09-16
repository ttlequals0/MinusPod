import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SegmentedToggle from './SegmentedToggle';

type Mode = 'one' | 'two' | 'three';
const OPTIONS = [
  { value: 'one' as const, label: 'One' },
  { value: 'two' as const, label: 'Two' },
  { value: 'three' as const, label: 'Three' },
];

function Harness({ initial = 'one' as Mode, onChange, disabled }: {
  initial?: Mode;
  onChange?: (value: Mode) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState<Mode>(initial);
  return (
    <SegmentedToggle
      options={OPTIONS}
      value={value}
      onChange={(next) => { setValue(next); onChange?.(next); }}
      ariaLabel="Mode"
      disabled={disabled}
    />
  );
}

describe('SegmentedToggle: radiogroup semantics', () => {
  it('exposes the options as radios with one tab stop on the selected one', () => {
    render(<Harness initial="two" />);
    expect(screen.getByRole('radiogroup', { name: 'Mode' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Two' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: 'One' }).getAttribute('tabindex')).toBe('-1');
    expect(screen.getByRole('radio', { name: 'Two' }).getAttribute('tabindex')).toBe('0');
  });

  it('commits a click on an unselected segment', async () => {
    const user = userEvent.setup();
    const changes: Mode[] = [];
    render(<Harness onChange={(v) => changes.push(v)} />);
    await user.click(screen.getByRole('radio', { name: 'Three' }));
    expect(changes).toEqual(['three']);
  });

  it('moves and commits with the arrow keys, wrapping at both ends', async () => {
    const user = userEvent.setup();
    const changes: Mode[] = [];
    render(<Harness onChange={(v) => changes.push(v)} />);

    screen.getByRole('radio', { name: 'One' }).focus();
    await user.keyboard('{ArrowLeft}');
    expect(changes).toEqual(['three']);
    expect(document.activeElement).toBe(screen.getByRole('radio', { name: 'Three' }));

    await user.keyboard('{ArrowDown}');
    expect(changes).toEqual(['three', 'one']);
  });

  it('jumps to the first and last segment with Home and End', async () => {
    const user = userEvent.setup();
    const changes: Mode[] = [];
    render(<Harness initial="two" onChange={(v) => changes.push(v)} />);

    screen.getByRole('radio', { name: 'Two' }).focus();
    await user.keyboard('{End}');
    await user.keyboard('{Home}');
    expect(changes).toEqual(['three', 'one']);
  });

  it('marks a still-inherited value muted instead of selected-primary', () => {
    const { rerender } = render(
      <SegmentedToggle options={OPTIONS} value="two" onChange={vi.fn()} ariaLabel="Mode" muted />,
    );
    expect(screen.getByRole('radio', { name: 'Two' }).className).toContain('bg-muted');
    rerender(<SegmentedToggle options={OPTIONS} value="two" onChange={vi.fn()} ariaLabel="Mode" />);
    expect(screen.getByRole('radio', { name: 'Two' }).className).toContain('bg-primary/10');
  });

  it('ignores clicks and arrow keys while disabled', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} disabled />);

    const first = screen.getByRole('radio', { name: 'One' });
    expect(first).toHaveProperty('disabled', true);
    await user.click(screen.getByRole('radio', { name: 'Two' }));
    first.focus();
    await user.keyboard('{ArrowRight}');
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('SegmentedToggle: toolbar variant', () => {
  it('is a labelled group of plain buttons, selection carried by aria-pressed', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SegmentedToggle
        options={[
          { value: 'recent', label: 'Recent', ariaLabel: 'Sort by recent', title: 'Sort by most recent episode' },
          { value: 'title', label: 'Title', ariaLabel: 'Sort by title' },
        ]}
        value="recent"
        onChange={onChange}
        ariaLabel="Sort"
        variant="toolbar"
      />,
    );

    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(screen.queryAllByRole('radio')).toHaveLength(0);
    expect(screen.getByRole('group', { name: 'Sort' })).toBeTruthy();
    const recent = screen.getByRole('button', { name: 'Sort by recent' });
    const title = screen.getByRole('button', { name: 'Sort by title' });
    expect(recent.getAttribute('aria-pressed')).toBe('true');
    expect(title.getAttribute('aria-pressed')).toBe('false');
    expect(recent.getAttribute('tabindex')).toBeNull();
    expect(recent.getAttribute('title')).toBe('Sort by most recent episode');

    await user.click(title);
    expect(onChange).toHaveBeenCalledWith('title');
    // No roving tabindex outside the radio pattern.
    recent.focus();
    await user.keyboard('{ArrowRight}');
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});

describe('SegmentedToggle: toolbar sizing', () => {
  const options = [
    { value: 'podcasts', label: 'Podcasts' },
    { value: 'episodes', label: 'Episodes' },
  ] as const;

  it('sizes a toolbar row to its labels so it fits a narrow phone', () => {
    render(
      <SegmentedToggle
        options={options}
        value="podcasts"
        onChange={() => {}}
        ariaLabel="Dashboard view"
        variant="toolbar"
      />,
    );

    const segment = screen.getByRole('button', { name: 'Podcasts' });
    expect(segment.className).toContain('px-2.5');
    expect(segment.className).toContain('sm:px-3');
    expect(segment.className).not.toContain('flex-1');
  });

  it('stretches a popover row across the container when asked', () => {
    render(
      <SegmentedToggle
        options={options}
        value="podcasts"
        onChange={() => {}}
        ariaLabel="Dashboard view"
        variant="toolbar"
        fill
      />,
    );

    const segment = screen.getByRole('button', { name: 'Podcasts' });
    expect(segment.className).toContain('flex-1');
    expect(screen.getByRole('group', { name: 'Dashboard view' }).className)
      .toContain('w-full');
  });
});
