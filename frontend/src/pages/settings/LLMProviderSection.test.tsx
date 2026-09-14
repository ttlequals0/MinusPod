import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LLMProviderSection from './LLMProviderSection';
import type { ProvidersResponse } from '../../api/providers';

const providersState: ProvidersResponse = {
  cryptoReady: true,
  anthropic: { configured: true, source: 'db' },
  openai: { configured: false, source: 'none' },
  openrouter: { configured: false, source: 'none' },
  whisper: { configured: false, source: 'none' },
  ollama: { configured: false, source: 'none' },
};

function renderSection(overrides: Partial<Parameters<typeof LLMProviderSection>[0]> = {}) {
  return render(
    <LLMProviderSection
      llmProvider="anthropic"
      openaiBaseUrl=""
      pricingSourceMode="auto"
      onProviderChange={vi.fn()}
      onBaseUrlChange={vi.fn()}
      onPricingSourceModeChange={vi.fn()}
      providersState={providersState}
      onProviderKeySave={vi.fn().mockResolvedValue(undefined)}
      onProviderKeyClear={vi.fn().mockResolvedValue(undefined)}
      onProviderKeyTest={vi.fn().mockResolvedValue({ ok: true })}
      onConnectionTest={vi.fn().mockResolvedValue({ ok: true, reachable: true, detail: 'OK' })}
      llmJsonSchemaEnabled={false}
      onLlmJsonSchemaEnabledChange={vi.fn()}
      secondaryProviderEnabled={false}
      onSecondaryProviderEnabledChange={vi.fn()}
      secondaryProvider="openrouter"
      onSecondaryProviderChange={vi.fn()}
      secondaryProviderBaseUrl=""
      onSecondaryProviderBaseUrlChange={vi.fn()}
      secondaryProviderApiKeyConfigured={false}
      onSecondaryProviderKeySave={vi.fn().mockResolvedValue(undefined)}
      onSecondaryProviderKeyClear={vi.fn().mockResolvedValue(undefined)}
      onSecondaryConnectionTest={vi.fn().mockResolvedValue({ ok: true, reachable: true, detail: 'OK' })}
      {...overrides}
    />,
  );
}

describe('LLMProviderSection: secondary provider toggle', () => {
  it('hides every secondary control until the toggle is on', () => {
    renderSection();
    expect(screen.queryByLabelText('Secondary provider type')).toBeNull();
    expect(screen.queryByLabelText('OpenRouter API key')).toBeNull();
  });

  it('fires only onSecondaryProviderEnabledChange when the toggle is clicked, leaving primary untouched', async () => {
    const user = userEvent.setup();
    const onSecondaryProviderEnabledChange = vi.fn();
    const onProviderChange = vi.fn();
    const onBaseUrlChange = vi.fn();
    renderSection({ onSecondaryProviderEnabledChange, onProviderChange, onBaseUrlChange });

    await user.click(screen.getByRole('switch', { name: 'Enable secondary provider' }));

    expect(onSecondaryProviderEnabledChange).toHaveBeenCalledWith(true);
    expect(onProviderChange).not.toHaveBeenCalled();
    expect(onBaseUrlChange).not.toHaveBeenCalled();
  });
});

describe('LLMProviderSection: secondary provider block, once enabled', () => {
  it('exposes the same controls as the primary block: type select, key field, connection test', () => {
    renderSection({ secondaryProviderEnabled: true });
    expect(screen.getByLabelText('Secondary provider type')).toBeDefined();
    expect(screen.getByLabelText('OpenRouter API key')).toBeDefined();
    expect(screen.getAllByRole('button', { name: 'Test connection' }).length).toBeGreaterThan(0);
  });

  it('shows a base URL field and its own connection test for a configurable-endpoint secondary type', async () => {
    const user = userEvent.setup();
    const onSecondaryConnectionTest = vi.fn().mockResolvedValue({ ok: true, reachable: true, detail: 'OK' });
    const onConnectionTest = vi.fn().mockResolvedValue({ ok: true, reachable: true, detail: 'OK' });
    renderSection({
      secondaryProviderEnabled: true,
      secondaryProvider: 'ollama',
      secondaryProviderBaseUrl: 'http://localhost:11434/v1',
      onSecondaryConnectionTest,
      onConnectionTest,
    });

    const baseUrlInput = screen.getByLabelText('Secondary base URL');
    expect(baseUrlInput).toBeDefined();
    const container = baseUrlInput.closest('div') as HTMLElement;
    await user.click(within(container).getByRole('button', { name: 'Test connection' }));

    expect(onSecondaryConnectionTest).toHaveBeenCalledWith('http://localhost:11434/v1');
    expect(onConnectionTest).not.toHaveBeenCalled();
  });

  it('routes the secondary type select to its own handler, not the primary one', async () => {
    const user = userEvent.setup();
    const onProviderChange = vi.fn();
    const onSecondaryProviderChange = vi.fn();
    renderSection({ secondaryProviderEnabled: true, onProviderChange, onSecondaryProviderChange });

    await user.selectOptions(screen.getByLabelText('Secondary provider type'), 'ollama');

    expect(onSecondaryProviderChange).toHaveBeenCalledWith('ollama');
    expect(onProviderChange).not.toHaveBeenCalled();
  });

  it('saves the secondary key through the secondary handler, not the primary one', async () => {
    const user = userEvent.setup();
    const onProviderKeySave = vi.fn().mockResolvedValue(undefined);
    const onSecondaryProviderKeySave = vi.fn().mockResolvedValue(undefined);
    renderSection({ secondaryProviderEnabled: true, onProviderKeySave, onSecondaryProviderKeySave });

    const keyInput = screen.getByLabelText('OpenRouter API key');
    await user.type(keyInput, 'sk-or-v1-secondary');
    const form = keyInput.closest('form') as HTMLElement;
    await user.click(within(form).getByRole('button', { name: 'Save' }));

    expect(onSecondaryProviderKeySave).toHaveBeenCalledWith('sk-or-v1-secondary');
    expect(onProviderKeySave).not.toHaveBeenCalled();
  });

  it('shows the configured key status independently of the primary key', () => {
    renderSection({ secondaryProviderEnabled: true, secondaryProviderApiKeyConfigured: true });
    const secondaryKeyField = screen.getByLabelText('OpenRouter API key').closest('form') as HTMLElement;
    expect(within(secondaryKeyField).getByText('Stored encrypted')).toBeDefined();
  });
});
