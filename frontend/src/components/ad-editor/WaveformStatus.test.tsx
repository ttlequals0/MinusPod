import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { WaveformError, WaveformLoading } from './WaveformStatus';

describe('WaveformStatus', () => {
  it('announces the loading line as a status', () => {
    render(<WaveformLoading />);
    expect(screen.getByRole('status').textContent).toBe('Loading waveform...');
  });

  it('announces the error as an alert and offers Retry', async () => {
    const onRetry = vi.fn();
    render(<WaveformError onRetry={onRetry} />);
    expect(screen.getByRole('alert').textContent).toBe('Could not load waveform. Try again.');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('gives Retry the shared ghost recipe and a 44 px touch floor', () => {
    render(<WaveformError onRetry={() => {}} />);
    const cls = screen.getByRole('button', { name: 'Retry' }).className;
    expect(cls).toContain('hover:bg-accent');
    expect(cls).toContain('touch-target');
    expect(cls).toContain('max-sm:min-w-11');
  });
});
