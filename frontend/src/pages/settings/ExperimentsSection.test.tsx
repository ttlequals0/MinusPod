/**
 * Tests for the Experiments group, which now holds only the ad addressing
 * mode; Ad reviewer moved to AI and processing.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ExperimentsSection from './ExperimentsSection';
import { expectGatedByLlm, expectNotGatedByLlm } from '../../test/llmGate';

describe('ExperimentsSection: addressing mode', () => {
  it('renders the addressing mode select with the current value', () => {
    render(
      <ExperimentsSection llmEnabled addressingMode="segment_ids" onAddressingModeChange={vi.fn()} />,
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
      <ExperimentsSection llmEnabled
        addressingMode="timestamps"
        onAddressingModeChange={onAddressingModeChange}
      />,
    );
    const select = screen.getByLabelText('Ad addressing mode');
    await user.selectOptions(select, 'segment_ids');
    expect(onAddressingModeChange).toHaveBeenCalledWith('segment_ids');
  });

  it('no longer renders the Ad reviewer controls', () => {
    render(<ExperimentsSection llmEnabled addressingMode="timestamps" onAddressingModeChange={vi.fn()} />);
    expect(screen.queryByText('Enable ad reviewer')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset reviewer prompts to default' })).toBeNull();
  });
});

describe('ExperimentsSection: LLM provider None', () => {
  it('disables the addressing mode with the reason', () => {
    render(<ExperimentsSection llmEnabled={false} addressingMode="timestamps" onAddressingModeChange={vi.fn()} />);
    expectGatedByLlm(screen.getByLabelText('Ad addressing mode'));
  });

  it('is enabled when the provider is set', () => {
    render(<ExperimentsSection llmEnabled addressingMode="timestamps" onAddressingModeChange={vi.fn()} />);
    expectNotGatedByLlm(screen.getByLabelText('Ad addressing mode'));
  });
});
