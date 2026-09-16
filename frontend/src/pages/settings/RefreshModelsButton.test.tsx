import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RefreshModelsButton from './RefreshModelsButton';
import { useModelsRefresh, type CatalogTarget } from '../../hooks/useModelsRefresh';
import * as settingsApi from '../../api/settings';

vi.mock('../../api/settings', () => ({
  refreshModels: vi.fn(),
  modelsQueryOptionsFor: (provider: string, slot: string) => ({
    queryKey: ['models', provider, slot],
  }),
}));

const PRIMARY: CatalogTarget = { provider: 'anthropic', slot: 'primary' };
const SECONDARY: CatalogTarget = { provider: 'openrouter', slot: 'secondary' };

function Harness({ targets, variant }: { targets: CatalogTarget[]; variant?: 'chip' | 'link' }) {
  const refresh = useModelsRefresh(targets);
  return (
    <>
      <RefreshModelsButton variant={variant} onClick={refresh.refresh} isPending={refresh.isPending} />
      <p data-testid="error">{refresh.error ?? ''}</p>
    </>
  );
}

function renderHarness(targets: CatalogTarget[], variant?: 'chip' | 'link') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  render(
    <QueryClientProvider client={queryClient}>
      <Harness targets={targets} variant={variant} />
    </QueryClientProvider>,
  );
  return { invalidate };
}

const button = () => screen.getByRole('button');
const click = () => act(async () => { button().click(); });
const errorText = () => screen.getByTestId('error').textContent;

describe('RefreshModelsButton', () => {
  beforeEach(() => { vi.mocked(settingsApi.refreshModels).mockReset(); });

  it('rebuilds each target slot once and invalidates only those catalogs', async () => {
    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    const { invalidate } = renderHarness([PRIMARY, { provider: 'anthropic', slot: 'primary' }, SECONDARY]);

    await click();

    await waitFor(() => expect(vi.mocked(settingsApi.refreshModels)).toHaveBeenCalledTimes(2));
    const slots = vi.mocked(settingsApi.refreshModels).mock.calls.map((c) => c[0]);
    expect(new Set(slots)).toEqual(new Set(['primary', 'secondary']));
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown }).queryKey);
    expect(keys).toEqual([
      ['models', 'anthropic', 'primary'],
      ['models', 'anthropic', 'primary'],
      ['models', 'openrouter', 'secondary'],
    ]);
  });

  it('shows the pending label and disables the button while a refresh runs', async () => {
    let release = () => {};
    vi.mocked(settingsApi.refreshModels).mockImplementation(
      () => new Promise((resolve) => { release = () => resolve({ models: [], count: 0 }); }),
    );
    renderHarness([PRIMARY]);

    expect(button().textContent).toContain('Refresh');
    expect(button()).toHaveProperty('disabled', false);

    await click();
    await waitFor(() => expect(button()).toHaveProperty('disabled', true));
    expect(button().textContent).toContain('Refreshing...');

    await act(async () => { release(); });
    await waitFor(() => expect(button()).toHaveProperty('disabled', false));
    expect(button().textContent).toContain('Refresh');
  });

  it('keeps a failed refresh visible until the next one succeeds', async () => {
    vi.mocked(settingsApi.refreshModels).mockRejectedValue(new Error('No secondary provider configured'));
    renderHarness([SECONDARY]);

    await click();
    await waitFor(() => expect(errorText()).toBe('No secondary provider configured'));

    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    await click();
    await waitFor(() => expect(errorText()).toBe(''));
  });

  it('labels the link variant for the single select it sits above', () => {
    renderHarness([PRIMARY], 'link');
    expect(screen.getByRole('button', { name: 'Refresh models' })).toBeTruthy();
  });
});
