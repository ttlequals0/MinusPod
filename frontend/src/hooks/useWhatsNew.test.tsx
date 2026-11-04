import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useWhatsNew, WHATS_NEW_SEEN_KEY } from './useWhatsNew';

const mockStatus = vi.fn();
const mockSettings = vi.fn();
const mockChangelog = vi.fn();

vi.mock('../api/settings', () => ({
  getHealth: () => mockStatus(),
  getSettings: () => mockSettings(),
  getChangelog: (p: unknown) => mockChangelog(p),
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  mockStatus.mockResolvedValue({ version: '2.101.17' });
  mockSettings.mockResolvedValue({ whatsNewOnUpgrade: { value: true, isDefault: true } });
  mockChangelog.mockResolvedValue({
    current: '2.101.17',
    sections: [{ version: '2.101.17', date: '2026-11-04', body: '- x' }],
  });
});

describe('useWhatsNew', () => {
  it('opens when the seen version differs, then stores the current version on close', async () => {
    localStorage.setItem(WHATS_NEW_SEEN_KEY, '2.101.14');
    const { result } = renderHook(() => useWhatsNew(true), { wrapper });
    await waitFor(() => expect(result.current.open).toBe(true));
    expect(mockChangelog).toHaveBeenCalledWith({ since: '2.101.14' });
    expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify('2.101.14'));
    act(() => result.current.close());
    expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify('2.101.17'));
    expect(result.current.open).toBe(false);
  });

  it('shows only the newest section on a fresh browser', async () => {
    const { result } = renderHook(() => useWhatsNew(true), { wrapper });
    await waitFor(() => expect(result.current.open).toBe(true));
    expect(mockChangelog).toHaveBeenCalledWith({ limit: 1 });
    expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify(''));
    act(() => result.current.close());
    expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify('2.101.17'));
  });

  it('stays silent on a fresh browser when the newest section is older than the app', async () => {
    mockChangelog.mockResolvedValue({
      current: '2.101.17',
      sections: [{ version: '2.101.16', date: '2026-11-03', body: '- x' }],
    });
    const { result } = renderHook(() => useWhatsNew(true), { wrapper });
    await waitFor(() => expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify('2.101.17')));
    expect(result.current.open).toBe(false);
  });

  it('records the version without fetching the changelog on a fresh browser that opted out', async () => {
    mockSettings.mockResolvedValue({ whatsNewOnUpgrade: { value: false, isDefault: false } });
    const { result } = renderHook(() => useWhatsNew(true), { wrapper });
    await waitFor(() => expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify('2.101.17')));
    expect(result.current.open).toBe(false);
    expect(mockChangelog).not.toHaveBeenCalled();
  });

  it('stays closed and records the version when the setting is off', async () => {
    localStorage.setItem(WHATS_NEW_SEEN_KEY, '2.101.14');
    mockSettings.mockResolvedValue({ whatsNewOnUpgrade: { value: false, isDefault: false } });
    const { result } = renderHook(() => useWhatsNew(true), { wrapper });
    await waitFor(() => expect(localStorage.getItem(WHATS_NEW_SEEN_KEY)).toBe(JSON.stringify('2.101.17')));
    expect(result.current.open).toBe(false);
    expect(mockChangelog).not.toHaveBeenCalled();
  });

  it('does nothing when the version is unchanged or the user is signed out', async () => {
    localStorage.setItem(WHATS_NEW_SEEN_KEY, '2.101.17');
    const same = renderHook(() => useWhatsNew(true), { wrapper });
    await waitFor(() => expect(mockStatus).toHaveBeenCalled());
    expect(same.result.current.open).toBe(false);
    expect(mockChangelog).not.toHaveBeenCalled();
    expect(mockSettings).not.toHaveBeenCalled();

    localStorage.setItem(WHATS_NEW_SEEN_KEY, '2.101.14');
    renderHook(() => useWhatsNew(false), { wrapper });
    expect(mockStatus).toHaveBeenCalledTimes(1);
  });
});
