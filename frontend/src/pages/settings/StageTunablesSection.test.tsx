/**
 * Tests for the "Do not send temperature" operator override toggle in the
 * LLM Tunables section.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StageTunablesSection from './StageTunablesSection';
import type { UpdateSettingsPayload } from '../../api/types';
import { baseDefaults, baseTunables } from './tunablesTestFixtures';

// openai-compatible renders the reasoning field as a <select> (not a number
// input), so each stage block contributes exactly two number inputs
// (Temperature, then Max tokens) in a stable, predictable order.
function Harness({
  omitTemperature = false,
  onSave = () => {},
}: {
  omitTemperature?: boolean;
  onSave?: (payload: UpdateSettingsPayload) => void;
}) {
  return (
    <StageTunablesSection
      tunables={baseTunables}
      defaults={baseDefaults}
      llmProvider="openai-compatible"
      onSave={onSave}
      saveIsPending={false}
      saveIsSuccess={false}
      saveError={null}
      parallelWindows={4}
      parallelWindowsDefault={4}
      omitTemperature={omitTemperature}
    />
  );
}

describe('StageTunablesSection: Do not send temperature toggle', () => {
  it('renders off by default', () => {
    render(<Harness />);
    const toggle = screen.getByRole('switch', { name: 'Do not send temperature' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('reflects an initial value of true', () => {
    render(<Harness omitTemperature />);
    const toggle = screen.getByRole('switch', { name: 'Do not send temperature' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });

  it('sends { omitTemperature: true } after switching on and saving', async () => {
    let saved: UpdateSettingsPayload | null = null;
    render(<Harness onSave={(payload) => { saved = payload; }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Do not send temperature' }));
    await user.click(screen.getByRole('button', { name: 'Save LLM Tunables' }));

    expect(saved).toEqual({ omitTemperature: true });
  });

  it('disables every per-stage temperature input while the toggle is on', () => {
    const { container } = render(<Harness omitTemperature />);
    const numberInputs = Array.from(
      container.querySelectorAll('input[type="number"]'),
    ) as HTMLInputElement[];
    const temperatureInputs = numberInputs.filter((_, i) => i % 2 === 0).slice(0, 5);
    const maxTokenInputs = numberInputs.filter((_, i) => i % 2 === 1).slice(0, 5);

    expect(temperatureInputs).toHaveLength(5);
    for (const input of temperatureInputs) {
      expect(input.disabled).toBe(true);
    }
    for (const input of maxTokenInputs) {
      expect(input.disabled).toBe(false);
    }
  });

  it('leaves the per-stage temperature inputs enabled while the toggle is off', () => {
    const { container } = render(<Harness />);
    const numberInputs = Array.from(
      container.querySelectorAll('input[type="number"]'),
    ) as HTMLInputElement[];
    const temperatureInputs = numberInputs.filter((_, i) => i % 2 === 0).slice(0, 5);

    expect(temperatureInputs).toHaveLength(5);
    for (const input of temperatureInputs) {
      expect(input.disabled).toBe(false);
    }
  });
});
