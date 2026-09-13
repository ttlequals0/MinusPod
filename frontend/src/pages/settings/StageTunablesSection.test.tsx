/**
 * Tests for the "Do not send temperature" operator override toggle in the
 * LLM Tunables section.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StageTunablesSection from './StageTunablesSection';
import type { LlmProvider, UpdateSettingsPayload } from '../../api/types';
import { baseDefaults, baseTunables } from './tunablesTestFixtures';

// openai-compatible renders the reasoning field as a <select> (not a number
// input), so each stage block contributes exactly two number inputs
// (Temperature, then Max tokens) in a stable, predictable order.
function Harness({
  omitTemperature = false,
  onSave = () => {},
  llmProvider = 'openai-compatible',
  detectionProvider = '',
  verificationProvider = '',
  chaptersProvider = '',
  reviewProvider = '',
}: {
  omitTemperature?: boolean;
  onSave?: (payload: UpdateSettingsPayload) => void;
  llmProvider?: LlmProvider;
  detectionProvider?: string;
  verificationProvider?: string;
  chaptersProvider?: string;
  reviewProvider?: string;
}) {
  return (
    <StageTunablesSection
      tunables={baseTunables}
      defaults={baseDefaults}
      llmProvider={llmProvider}
      detectionProvider={detectionProvider}
      verificationProvider={verificationProvider}
      chaptersProvider={chaptersProvider}
      reviewProvider={reviewProvider}
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

// Each stage block renders its own bordered card headed by an <h4>; scoping
// assertions to that card (rather than the whole page) is what lets these
// tests tell one stage's control apart from another's.
function stageCard(label: string) {
  return screen.getByRole('heading', { name: label, level: 4 }).closest('.border') as HTMLElement;
}

describe('StageTunablesSection: per-stage effective provider', () => {
  it('shows the Anthropic control for a stage on the global provider and the generic control for one routed elsewhere', () => {
    render(<Harness llmProvider="anthropic" verificationProvider="ollama" />);

    const detection = stageCard('Ad Detection (Pass 1)');
    expect(within(detection).getByText('Reasoning budget (Anthropic)')).toBeDefined();
    expect(within(detection).queryByText('Reasoning effort')).toBeNull();

    const verification = stageCard('Verification (Ad Detection Pass 2)');
    expect(within(verification).getByText('Reasoning effort')).toBeDefined();
    expect(within(verification).queryByText('Reasoning budget (Anthropic)')).toBeNull();
  });

  it('routing verification to another provider does not change the chapters or detection blocks', () => {
    render(<Harness llmProvider="anthropic" verificationProvider="ollama" />);

    expect(within(stageCard('Ad Detection (Pass 1)')).getByText('Reasoning budget (Anthropic)')).toBeDefined();
    expect(within(stageCard('Chapter Title Generation')).getByText('Reasoning budget (Anthropic)')).toBeDefined();
    expect(within(stageCard('Chapter Boundary Detection')).getByText('Reasoning budget (Anthropic)')).toBeDefined();
  });

  it('routes chapters to its own override independently of verification', () => {
    render(<Harness llmProvider="anthropic" verificationProvider="ollama" chaptersProvider="openrouter" />);

    // openrouter is non-Anthropic, same generic control as ollama, but the
    // point is chapters picked up its OWN override, not verification's.
    expect(within(stageCard('Chapter Title Generation')).getByText('Reasoning effort')).toBeDefined();
    expect(within(stageCard('Verification (Ad Detection Pass 2)')).getByText('Reasoning effort')).toBeDefined();
    expect(within(stageCard('Ad Detection (Pass 1)')).getByText('Reasoning budget (Anthropic)')).toBeDefined();
  });
});
