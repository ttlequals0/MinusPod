/**
 * Tests for the AI Models settings section, including the not-configured
 * placeholder shown when a model setting is an empty string (requires
 * an explicit LLM model instead of a hardcoded fallback).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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
    expect(screen.getByLabelText('Ad Detection Model').tagName).toBe('INPUT');

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
