/**
 * Tests for the AI Models settings section, including the not-configured
 * placeholder shown when a model setting is an empty string (requires
 * an explicit LLM model instead of a hardcoded fallback).
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AIModelsSection from './AIModelsSection';
import type { ClaudeModel } from '../../api/types';

const models: ClaudeModel[] = [
  { id: 'gpt-5', name: 'GPT-5' },
  { id: 'gpt-5-mini', name: 'GPT-5 Mini' },
];

function renderSection(overrides: Partial<Parameters<typeof AIModelsSection>[0]> = {}) {
  return render(
    <AIModelsSection
      models={models}
      modelsLoading={false}
      selectedModel="gpt-5"
      verificationModel="gpt-5"
      chaptersModel="gpt-5-mini"
      onSelectedModelChange={() => {}}
      onVerificationModelChange={() => {}}
      onChaptersModelChange={() => {}}
      onRefresh={() => {}}
      refreshIsPending={false}
      modelPricingOverrides={{}}
      onPricingOverrideUpdate={vi.fn().mockResolvedValue(undefined)}
      {...overrides}
    />
  );
}

describe('AIModelsSection: not-configured state', () => {
  it('renders a selected "Not configured" placeholder for an empty model value', () => {
    renderSection({ selectedModel: '' });
    const select = screen.getByLabelText('Ad Detection Model') as HTMLSelectElement;
    expect(select.value).toBe('');
    const placeholder = screen.getByRole('option', { name: 'Not configured' }) as HTMLOptionElement;
    expect(placeholder.selected).toBe(true);
  });

  it('shows the helper line under an unconfigured select', () => {
    renderSection({ selectedModel: '' });
    expect(screen.getByText('Pick a model before processing episodes.')).toBeDefined();
  });

  it('renders a configured value with no placeholder option and no helper line', () => {
    renderSection({ selectedModel: 'gpt-5' });
    const select = screen.getByLabelText('Ad Detection Model') as HTMLSelectElement;
    expect(select.value).toBe('gpt-5');
    expect(screen.queryByRole('option', { name: 'Not configured' })).toBeNull();
    expect(screen.queryByText('Pick a model before processing episodes.')).toBeNull();
  });

  it('applies the not-configured state independently to each model select', () => {
    renderSection({ selectedModel: 'gpt-5', verificationModel: '', chaptersModel: 'gpt-5-mini' });
    expect((screen.getByLabelText('Ad Detection Model') as HTMLSelectElement).value).toBe('gpt-5');
    expect((screen.getByLabelText('Verification Model') as HTMLSelectElement).value).toBe('');
    expect((screen.getByLabelText('Chapters Model') as HTMLSelectElement).value).toBe('gpt-5-mini');
    // One helper line per unconfigured select; here only Verification Model is empty.
    expect(screen.getAllByText('Pick a model before processing episodes.')).toHaveLength(1);
  });
});

describe('AIModelsSection: orphaned saved value', () => {
  it('still renders the existing "(current, not in catalog)" option for a value missing from the catalog', () => {
    renderSection({ selectedModel: 'retired-model' });
    expect(screen.getByRole('option', { name: 'retired-model (current, not in catalog)' })).toBeDefined();
    const select = screen.getByLabelText('Ad Detection Model') as HTMLSelectElement;
    expect(select.value).toBe('retired-model');
  });
});

describe('AIModelsSection: empty catalog banner', () => {
  it('still shows the empty-catalog banner when no models are available', () => {
    renderSection({ models: [], selectedModel: '', verificationModel: '', chaptersModel: '' });
    expect(
      screen.getByText('No models available from the LLM provider. Check that your provider is configured correctly and the endpoint is reachable.')
    ).toBeDefined();
  });
});

describe('AIModelsSection: typing a model ID', () => {
  it('swaps the list for a text field and back', async () => {
    const user = userEvent.setup();
    renderSection();
    expect(screen.getByLabelText('Ad Detection Model').tagName).toBe('SELECT');

    await user.click(screen.getAllByRole('button', { name: 'Type a model ID' })[0]);
    const input = screen.getByLabelText('Ad Detection Model');
    expect(input.tagName).toBe('INPUT');
    expect(input.className).toContain('min-h-[44px]');

    await user.click(screen.getAllByRole('button', { name: 'Choose from list' })[0]);
    expect(screen.getByLabelText('Ad Detection Model').tagName).toBe('SELECT');
  });

  it('reports what was typed', async () => {
    const user = userEvent.setup();
    const onSelectedModelChange = vi.fn();
    renderSection({ selectedModel: '', onSelectedModelChange });

    await user.click(screen.getAllByRole('button', { name: 'Type a model ID' })[0]);
    await user.type(screen.getByLabelText('Ad Detection Model'), 'x');

    expect(onSelectedModelChange).toHaveBeenCalledWith('x');
  });

  it('switches only the field whose button was clicked', async () => {
    const user = userEvent.setup();
    renderSection();

    await user.click(screen.getAllByRole('button', { name: 'Type a model ID' })[0]);

    expect(screen.getByLabelText('Ad Detection Model').tagName).toBe('INPUT');
    expect(screen.getByLabelText('Verification Model').tagName).toBe('SELECT');
    expect(screen.getByLabelText('Chapters Model').tagName).toBe('SELECT');
  });

  it('keeps the orphan option available in list mode', () => {
    renderSection({ selectedModel: 'retired-model' });
    expect(screen.getByLabelText('Ad Detection Model').tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'retired-model (current, not in catalog)' })).toBeDefined();
  });
});

describe('AIModelsSection: custom pricing', () => {
  it('renders explicit zero rates for a free model', () => {
    renderSection({
      modelPricingOverrides: {
        'gpt-5': { inputCostPerMtok: 0, outputCostPerMtok: 0 },
      },
    });

    const inputs = screen.getAllByLabelText('Input, USD per 1 million tokens') as HTMLInputElement[];
    const outputs = screen.getAllByLabelText('Output, USD per 1 million tokens') as HTMLInputElement[];
    expect(inputs[0].value).toBe('0');
    expect(outputs[0].value).toBe('0');
  });

  it('uses 44 px pricing inputs with the shared focus ring', () => {
    renderSection();

    const inputs = [
      ...screen.getAllByLabelText('Input, USD per 1 million tokens'),
      ...screen.getAllByLabelText('Output, USD per 1 million tokens'),
    ];
    for (const input of inputs) {
      expect(input.className).toContain('min-h-[44px]');
      expect(input.className).toContain('focus-visible:ring-2');
    }
  });

  it('saves both positive rates for the selected model', async () => {
    const user = userEvent.setup();
    const onPricingOverrideUpdate = vi.fn().mockResolvedValue(undefined);
    renderSection({ onPricingOverrideUpdate });

    const input = screen.getAllByLabelText('Input, USD per 1 million tokens')[0];
    const output = screen.getAllByLabelText('Output, USD per 1 million tokens')[0];
    await user.type(input, '1.25');
    await user.type(output, '4.5');
    await user.click(screen.getAllByRole('button', { name: 'Save pricing' })[0]);

    expect(onPricingOverrideUpdate).toHaveBeenCalledWith('gpt-5', {
      inputCostPerMtok: 1.25,
      outputCostPerMtok: 4.5,
    });
  });

  it('requires both rates', async () => {
    const user = userEvent.setup();
    const onPricingOverrideUpdate = vi.fn().mockResolvedValue(undefined);
    renderSection({ onPricingOverrideUpdate });

    await user.type(screen.getAllByLabelText('Input, USD per 1 million tokens')[0], '1');
    await user.click(screen.getAllByRole('button', { name: 'Save pricing' })[0]);

    expect(screen.getByText('Enter both prices, or leave both blank.')).toBeDefined();
    expect(onPricingOverrideUpdate).not.toHaveBeenCalled();
  });

  it('rejects negative rates before saving', async () => {
    const user = userEvent.setup();
    const onPricingOverrideUpdate = vi.fn().mockResolvedValue(undefined);
    renderSection({ onPricingOverrideUpdate });

    fireEvent.change(screen.getAllByLabelText('Input, USD per 1 million tokens')[0], {
      target: { value: '-1' },
    });
    fireEvent.change(screen.getAllByLabelText('Output, USD per 1 million tokens')[0], {
      target: { value: '2' },
    });
    await user.click(screen.getAllByRole('button', { name: 'Save pricing' })[0]);

    expect(screen.getByText('Prices must be non-negative numbers.')).toBeDefined();
    expect(onPricingOverrideUpdate).not.toHaveBeenCalled();
  });

  it('clears an override when both rates are blank', async () => {
    const user = userEvent.setup();
    const onPricingOverrideUpdate = vi.fn().mockResolvedValue(undefined);
    renderSection({
      modelPricingOverrides: {
        'gpt-5': { inputCostPerMtok: 1, outputCostPerMtok: 2 },
      },
      onPricingOverrideUpdate,
    });

    await user.clear(screen.getAllByLabelText('Input, USD per 1 million tokens')[0]);
    await user.clear(screen.getAllByLabelText('Output, USD per 1 million tokens')[0]);
    await user.click(screen.getAllByRole('button', { name: 'Save pricing' })[0]);

    expect(onPricingOverrideUpdate).toHaveBeenCalledWith('gpt-5', null);
  });

  it('keeps an override available after the model is no longer selected', async () => {
    const user = userEvent.setup();
    const onPricingOverrideUpdate = vi.fn().mockResolvedValue(undefined);
    renderSection({
      modelPricingOverrides: {
        'retired-model': { inputCostPerMtok: 1, outputCostPerMtok: 2 },
      },
      onPricingOverrideUpdate,
    });

    const retiredModel = screen.getByText('retired-model');
    const fields = retiredModel.closest('fieldset')!;
    const input = fields.querySelector<HTMLInputElement>('input[id$="-input"]')!;
    const output = fields.querySelector<HTMLInputElement>('input[id$="-output"]')!;
    await user.clear(input);
    await user.clear(output);
    await user.click(fields.querySelector<HTMLButtonElement>('button')!);

    expect(onPricingOverrideUpdate).toHaveBeenCalledWith('retired-model', null);
  });
});
