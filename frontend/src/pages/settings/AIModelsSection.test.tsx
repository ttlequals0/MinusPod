/**
 * Tests for the AI Models settings section, including the not-configured
 * placeholder shown when a model setting is an empty string (requires
 * an explicit LLM model instead of a hardcoded fallback).
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AIModelsSection from './AIModelsSection';
import type { ModelCatalog } from '../../hooks/useModelCatalog';
import type { ClaudeModel } from '../../api/types';

const models: ClaudeModel[] = [
  { id: 'gpt-5', name: 'GPT-5' },
  { id: 'gpt-5-mini', name: 'GPT-5 Mini' },
];

function catalog(overrides: Partial<ModelCatalog> = {}): ModelCatalog {
  return { models, isLoading: false, isError: false, ...overrides };
}

function renderSection(overrides: Partial<Parameters<typeof AIModelsSection>[0]> = {}) {
  return render(
    <AIModelsSection
      detectionCatalog={catalog()}
      verificationCatalog={catalog()}
      chaptersCatalog={catalog()}
      selectedModel="gpt-5"
      verificationModel="gpt-5"
      chaptersModel="gpt-5-mini"
      onSelectedModelChange={() => {}}
      onVerificationModelChange={() => {}}
      onChaptersModelChange={() => {}}
      detectionProvider="primary"
      verificationProvider="same_as_detection"
      chaptersProvider="same_as_detection"
      onDetectionProviderChange={() => {}}
      onVerificationProviderChange={() => {}}
      onChaptersProviderChange={() => {}}
      modelsRefresh={{ refresh: () => {}, isPending: false, error: null }}
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
    renderSection({
      detectionCatalog: catalog({ models: [] }),
      selectedModel: '', verificationModel: '', chaptersModel: '',
    });
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

describe('AIModelsSection: per-stage provider selects', () => {
  it('renders a provider select beside each stage\'s model select', () => {
    renderSection();
    expect(screen.getByLabelText('Ad Detection Provider')).toBeDefined();
    expect(screen.getByLabelText('Verification Provider')).toBeDefined();
    expect(screen.getByLabelText('Chapters Provider')).toBeDefined();
  });

  it('defaults detection to "Default (Primary)" and the others to "Same as detection"', () => {
    renderSection();
    expect((screen.getByLabelText('Ad Detection Provider') as HTMLSelectElement).selectedOptions[0].textContent)
      .toBe('Default (Primary)');
    expect((screen.getByLabelText('Verification Provider') as HTMLSelectElement).selectedOptions[0].textContent)
      .toBe('Same as detection');
    expect((screen.getByLabelText('Chapters Provider') as HTMLSelectElement).selectedOptions[0].textContent)
      .toBe('Same as detection');
  });

  it('changing the verification provider does not call the detection provider handler', async () => {
    const user = userEvent.setup();
    const onDetectionProviderChange = vi.fn();
    const onVerificationProviderChange = vi.fn();
    renderSection({ onDetectionProviderChange, onVerificationProviderChange });

    await user.selectOptions(screen.getByLabelText('Verification Provider'), 'primary');

    expect(onVerificationProviderChange).toHaveBeenCalledWith('primary');
    expect(onDetectionProviderChange).not.toHaveBeenCalled();
  });

  it('lists the verification-specific catalog once its provider diverges from detection', () => {
    renderSection({
      verificationProvider: 'secondary',
      verificationCatalog: catalog({ models: [{ id: 'llama3', name: 'Llama 3' }] }),
      verificationModel: 'llama3',
    });
    const select = screen.getByLabelText('Verification Model') as HTMLSelectElement;
    expect(select.value).toBe('llama3');
    expect(within(select).getByRole('option', { name: 'Llama 3' })).toBeDefined();
    expect(within(select).queryByRole('option', { name: 'GPT-5' })).toBeNull();
  });

  it('never borrows the detection catalog for a stage whose own list is missing', () => {
    renderSection({ verificationCatalog: catalog({ models: undefined }) });
    const verifSelect = screen.getByLabelText('Verification Model') as HTMLSelectElement;
    expect(within(verifSelect).queryByRole('option', { name: 'GPT-5' })).toBeNull();
    // The saved id stays selected so the field does not read as reset.
    expect(verifSelect.value).toBe('gpt-5');
  });
});

describe('AIModelsSection: secondary provider slot', () => {
  it('hides the Secondary option on every stage select while the secondary provider is off', () => {
    renderSection();
    for (const label of ['Ad Detection Provider', 'Verification Provider', 'Chapters Provider']) {
      expect(within(screen.getByLabelText(label)).queryByRole('option', { name: 'Secondary' })).toBeNull();
    }
  });

  it('shows the Secondary option once the secondary provider is enabled', () => {
    renderSection({ secondaryProviderEnabled: true });
    for (const label of ['Ad Detection Provider', 'Verification Provider', 'Chapters Provider']) {
      expect(within(screen.getByLabelText(label)).getByRole('option', { name: 'Secondary' })).toBeDefined();
    }
  });

  it('stores the slot value when Secondary is picked', async () => {
    const user = userEvent.setup();
    const onDetectionProviderChange = vi.fn();
    renderSection({ secondaryProviderEnabled: true, onDetectionProviderChange });

    await user.selectOptions(screen.getByLabelText('Ad Detection Provider'), 'Secondary');

    expect(onDetectionProviderChange).toHaveBeenCalledWith('secondary');
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

describe('AIModelsSection: per-stage catalog states', () => {
  it('shows a loading line rather than the detection catalog while a stage catalog is in flight', () => {
    renderSection({
      secondaryProviderEnabled: true,
      verificationProvider: 'secondary',
      verificationCatalog: catalog({ models: undefined, isLoading: true }),
      verificationModel: 'llama3',
    });
    const select = screen.getByLabelText('Verification Model') as HTMLSelectElement;
    expect(within(select).queryByRole('option', { name: 'GPT-5' })).toBeNull();
    expect(select.value).toBe('llama3');
    expect(screen.getByText('Loading models...')).toBeDefined();
  });

  it('shows an error line rather than the detection catalog when a stage catalog fails', () => {
    renderSection({
      secondaryProviderEnabled: true,
      verificationProvider: 'secondary',
      verificationCatalog: catalog({ models: undefined, isError: true }),
      verificationModel: 'llama3',
    });
    const select = screen.getByLabelText('Verification Model') as HTMLSelectElement;
    expect(within(select).queryByRole('option', { name: 'GPT-5' })).toBeNull();
    expect(screen.getByText("Could not load this provider's model list. Refresh to try again.")).toBeDefined();
  });

  it('reports the detection catalog state on its own select', () => {
    renderSection({
      detectionCatalog: catalog({ models: undefined, isLoading: true }),
      selectedModel: 'gpt-5',
    });
    const select = screen.getByLabelText('Ad Detection Model') as HTMLSelectElement;
    expect(select.value).toBe('gpt-5');
    expect(screen.getAllByText('Loading models...')).toHaveLength(1);
  });

  it('shows an error line when the detection catalog fails', () => {
    renderSection({ detectionCatalog: catalog({ models: undefined, isError: true }) });
    expect(screen.getAllByText("Could not load this provider's model list. Refresh to try again.")).toHaveLength(1);
  });

  it('reports the chapters catalog state without touching the other stages', () => {
    renderSection({
      secondaryProviderEnabled: true,
      chaptersProvider: 'secondary',
      chaptersCatalog: catalog({ models: undefined, isError: true }),
    });
    expect(screen.getAllByText("Could not load this provider's model list. Refresh to try again.")).toHaveLength(1);
    expect(screen.queryByText('Loading models...')).toBeNull();
  });
});

describe('AIModelsSection: stored secondary slot while the secondary provider is off', () => {
  it('keeps the stored value selected instead of reading as Default (Primary)', () => {
    renderSection({ detectionProvider: 'secondary' });
    const select = screen.getByLabelText('Ad Detection Provider') as HTMLSelectElement;
    expect(select.value).toBe('secondary');
    expect(select.selectedOptions[0].textContent).toBe('Secondary (provider off)');
  });

  it('explains which provider the stage actually runs on', () => {
    renderSection({ detectionProvider: 'secondary' });
    expect(screen.getByText('Secondary provider is off, so this stage runs on the primary.')).toBeDefined();
  });

  it('leaves stages that are not on the secondary slot alone', () => {
    renderSection({ detectionProvider: 'secondary' });
    expect(
      within(screen.getByLabelText('Verification Provider')).queryByRole('option', { name: 'Secondary (provider off)' })
    ).toBeNull();
    expect(screen.getAllByText('Secondary provider is off, so this stage runs on the primary.')).toHaveLength(1);
  });
});
