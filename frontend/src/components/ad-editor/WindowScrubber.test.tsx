import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WindowScrubber } from './WindowScrubber';

function setup() {
  const onPan = vi.fn();
  render(
    <WindowScrubber
      ariaLabel="Episode position"
      className=""
      totalDuration={1000}
      windowStart={400}
      windowEnd={600}
      windowCenter={500}
      playheadTime={500}
      onPan={onPan}
    />,
  );
  return onPan;
}

describe('WindowScrubber keyboard', () => {
  it('pans by a quarter window with the arrow keys', async () => {
    const onPan = setup();
    screen.getByRole('slider', { name: 'Episode position' }).focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(onPan).toHaveBeenLastCalledWith(550);
    await userEvent.keyboard('{ArrowLeft}');
    expect(onPan).toHaveBeenLastCalledWith(450);
  });

  it('jumps to the ends with Home and End', async () => {
    const onPan = setup();
    screen.getByRole('slider', { name: 'Episode position' }).focus();
    await userEvent.keyboard('{End}');
    expect(onPan).toHaveBeenLastCalledWith(1000);
    await userEvent.keyboard('{Home}');
    expect(onPan).toHaveBeenLastCalledWith(0);
  });
});
