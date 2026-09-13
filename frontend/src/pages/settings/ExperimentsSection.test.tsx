/**
 * Tests for the Experiments group, which now holds only the ad addressing
 * mode; Ad Reviewer moved to AI & Processing.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ExperimentsSection from './ExperimentsSection';

describe('ExperimentsSection: addressing mode', () => {
  it('renders the addressing mode select with the current value', () => {
    render(
      <ExperimentsSection addressingMode="segment_ids" onAddressingModeChange={vi.fn()} />,
    );
    const select = screen.getByLabelText('Ad addressing mode') as HTMLSelectElement;
    expect(select.value).toBe('segment_ids');
    expect(screen.getByRole('option', { name: 'Timestamps (default)' })).toBeDefined();
    expect(screen.getByRole('option', { name: 'Segment IDs' })).toBeDefined();
  });

  it('fires onAddressingModeChange when the select changes', async () => {
    const onAddressingModeChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ExperimentsSection
        addressingMode="timestamps"
        onAddressingModeChange={onAddressingModeChange}
      />,
    );
    const select = screen.getByLabelText('Ad addressing mode');
    await user.selectOptions(select, 'segment_ids');
    expect(onAddressingModeChange).toHaveBeenCalledWith('segment_ids');
  });

  it('no longer renders the Ad Reviewer controls', () => {
    render(<ExperimentsSection addressingMode="timestamps" onAddressingModeChange={vi.fn()} />);
    expect(screen.queryByText('Enable ad reviewer')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset Reviewer Prompts to Default' })).toBeNull();
  });
});
