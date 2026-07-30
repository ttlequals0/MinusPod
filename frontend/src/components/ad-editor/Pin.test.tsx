import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Pin } from './Pin';

function renderPin(over: Partial<React.ComponentProps<typeof Pin>> = {}) {
  const onChange = vi.fn();
  const containerRef = createRef<HTMLDivElement>();
  render(
    <div ref={containerRef} style={{ width: 1000 }}>
      <Pin
        kind="start"
        boundary={50}
        windowStart={0}
        windowDuration={100}
        containerRef={containerRef}
        onChange={onChange}
        otherBoundary={90}
        {...over}
      />
    </div>,
  );
  return { onChange, pin: screen.getByRole('slider') };
}

describe('Pin keyboard operation', () => {
  it('is focusable, so the announced slider can be used', () => {
    const { pin } = renderPin();
    expect(pin.getAttribute('tabIndex')).toBe('0');
  });

  it('nudges forward on ArrowRight', async () => {
    const { onChange, pin } = renderPin();
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{ArrowRight}');
    expect(onChange).toHaveBeenCalledWith(50.1);
  });

  it('nudges back on ArrowLeft', async () => {
    const { onChange, pin } = renderPin();
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{ArrowLeft}');
    expect(onChange).toHaveBeenCalledWith(49.9);
  });

  it('takes a coarser step with Shift held', async () => {
    const { onChange, pin } = renderPin();
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(51);
  });

  it('ignores keys that are not arrows', async () => {
    const { onChange, pin } = renderPin();
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('a');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('exposes the range it is allowed to move in', () => {
    const { pin } = renderPin();
    // start clamps below end minus the separation floor.
    expect(pin.getAttribute('aria-valuemax')).toBe('89');
    expect(pin.getAttribute('aria-valuenow')).toBe('50');
  });

  it('a start pin cannot be nudged across the end pin', async () => {
    const { onChange, pin } = renderPin({ boundary: 89, otherBoundary: 90 });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(89);
  });
});

describe('Pin absolute clamps', () => {
  it('a start pin cannot be nudged below zero', async () => {
    const { onChange, pin } = renderPin({ boundary: 0.5 });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowLeft}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(0);
  });

  it('an end pin cannot be nudged past the audio end', async () => {
    const { onChange, pin } = renderPin({
      kind: 'end', boundary: 99.5, otherBoundary: 50, totalDuration: 100,
    });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(100);
  });

  it('skips the neighbour clamp when its bounds are inverted', async () => {
    // A seeded piece already under the floor: lower (107) > upper (96).
    const { onChange, pin } = renderPin({
      kind: 'divider', otherBoundary: undefined, boundary: 101.5,
      minBoundary: 100, maxBoundary: 103, minSeparation: 7,
      windowStart: 50, windowDuration: 100,
    });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(102.5);
  });

  it('still clamps to the audio end when the neighbour bounds are inverted', async () => {
    const { onChange, pin } = renderPin({
      kind: 'divider', otherBoundary: undefined, boundary: 101.5,
      minBoundary: 100, maxBoundary: 103, minSeparation: 7,
      windowStart: 50, windowDuration: 100, totalDuration: 102,
    });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(102);
  });
});

describe('Pin divider kind', () => {
  function renderDivider(over: Partial<React.ComponentProps<typeof Pin>> = {}) {
    return renderPin({
      kind: 'divider',
      boundary: 50,
      otherBoundary: undefined,
      minBoundary: 20,
      maxBoundary: 80,
      ...over,
    });
  }

  it('clamps against its lower neighbour', async () => {
    const { onChange, pin } = renderDivider({ boundary: 21 });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowLeft}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(21);
  });

  it('clamps against its upper neighbour', async () => {
    const { onChange, pin } = renderDivider({ boundary: 79 });
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onChange).toHaveBeenCalledWith(79);
  });

  it('moves freely between its neighbours', async () => {
    const { onChange, pin } = renderDivider();
    const user = userEvent.setup();
    pin.focus();
    await user.keyboard('{ArrowRight}');
    expect(onChange).toHaveBeenCalledWith(50.1);
  });

  it('announces itself as a split point with both bounds', () => {
    const { pin } = renderDivider();
    expect(pin.getAttribute('aria-label')).toContain('SPLIT');
    expect(pin.getAttribute('aria-valuemin')).toBe('21');
    expect(pin.getAttribute('aria-valuemax')).toBe('79');
  });
});
