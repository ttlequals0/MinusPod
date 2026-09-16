import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
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
      providerRequestsPerMin={0}
      onProviderRequestsPerMinChange={vi.fn()}
      providerRequestsPerDay={0}
      onProviderRequestsPerDayChange={vi.fn()}
      secondaryProviderRequestsPerMin={0}
      onSecondaryProviderRequestsPerMinChange={vi.fn()}
      secondaryProviderRequestsPerDay={0}
      onSecondaryProviderRequestsPerDayChange={vi.fn()}
      providerTokensPerMin={0}
      onProviderTokensPerMinChange={vi.fn()}
      secondaryProviderTokensPerMin={0}
      onSecondaryProviderTokensPerMinChange={vi.fn()}
      primaryAccountChanged={false}
      secondaryAccountChanged={false}
      affectedRunsAction="requeue"
      onAffectedRunsActionChange={vi.fn()}
      {...overrides}
    />
    </QueryClientProvider>,
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

  it('offers a placeholder instead of a silent first option when no type is saved', () => {
    renderSection({ secondaryProviderEnabled: true, secondaryProvider: '' });
    const select = screen.getByLabelText('Secondary provider type') as HTMLSelectElement;
    expect(select.value).toBe('');
    expect(within(select).getByRole('option', { name: 'Choose a provider' })).toBeDefined();
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

    expect(onSecondaryConnectionTest).toHaveBeenCalledWith('ollama', 'http://localhost:11434/v1');
    expect(onConnectionTest).not.toHaveBeenCalled();
  });

  it('passes the current secondary provider type to the connection test for a fixed-endpoint type', async () => {
    const user = userEvent.setup();
    const onSecondaryConnectionTest = vi.fn().mockResolvedValue({ ok: true, reachable: true, detail: 'OK' });
    renderSection({
      secondaryProviderEnabled: true,
      secondaryProvider: 'openrouter',
      onSecondaryConnectionTest,
    });

    // Both the primary (default "anthropic") and secondary blocks are
    // fixed-endpoint types here, so scope to the secondary block's own
    // container rather than matching either "Test connection" button.
    const secondaryContainer = screen.getByLabelText('Secondary provider type').closest('.space-y-4') as HTMLElement;
    await user.click(within(secondaryContainer).getByRole('button', { name: 'Test connection' }));

    expect(onSecondaryConnectionTest).toHaveBeenCalledWith('openrouter', undefined);
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

describe('LLMProviderSection: manual request-rate limits', () => {
  it('renders primary RPM/RPD/TPM inputs and fires the primary change handlers', async () => {
    const user = userEvent.setup();
    const onProviderRequestsPerMinChange = vi.fn();
    const onProviderTokensPerMinChange = vi.fn();
    renderSection({ onProviderRequestsPerMinChange, onProviderTokensPerMinChange });

    const rpm = screen.getByLabelText('Requests per minute') as HTMLInputElement;
    expect(screen.getByLabelText('Requests per day')).toBeDefined();
    const tpm = screen.getByLabelText('Tokens per minute') as HTMLInputElement;
    await user.type(rpm, '5');
    expect(onProviderRequestsPerMinChange).toHaveBeenCalledWith(5);
    await user.type(tpm, '250000');
    expect(onProviderTokensPerMinChange).toHaveBeenCalledWith(250000);
  });

  it('renders the limit inputs for every primary provider type', () => {
    for (const provider of ['anthropic', 'openai-compatible', 'ollama', 'openrouter'] as const) {
      const { unmount } = renderSection({ llmProvider: provider });
      expect(screen.getByLabelText('Requests per minute')).toBeDefined();
      expect(screen.getByLabelText('Requests per day')).toBeDefined();
      expect(screen.getByLabelText('Tokens per minute')).toBeDefined();
      unmount();
    }
  });

  it('shows one RPM input with the secondary provider off', () => {
    renderSection();
    expect(screen.getAllByLabelText('Requests per minute').length).toBe(1);
  });

  it('shows a second RPM input once the secondary provider is on', () => {
    renderSection({ secondaryProviderEnabled: true });
    expect(screen.getAllByLabelText('Requests per minute').length).toBe(2);
  });
});

describe('LLMProviderSection: rate-limit grouping and field chrome', () => {
  it('separates the primary and secondary limits with their own legends', () => {
    renderSection({ secondaryProviderEnabled: true });
    const groups = screen.getAllByRole('group');
    const legends = groups.map((g) => g.querySelector('legend')?.textContent);
    expect(legends).toContain('Primary provider rate limits');
    expect(legends).toContain('Secondary provider rate limits');
  });

  it('gives the base URL field the shared input chrome', () => {
    renderSection({ llmProvider: 'ollama' });
    const input = screen.getByLabelText('Base URL');
    expect(input.className).toContain('px-3 py-2');
    expect(input.className).not.toContain('px-4');
  });
});
