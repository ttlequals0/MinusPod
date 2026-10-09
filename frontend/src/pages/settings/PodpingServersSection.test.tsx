import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PodpingServersSection from './PodpingServersSection';
import type { PodpingNodes } from '../../api/podping';

const { mockGet, mockUpdate, mockReset } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockUpdate: vi.fn(),
  mockReset: vi.fn(),
}));

vi.mock('../../api/podping', () => ({
  podpingNodesQueryKey: ['podping', 'nodes'],
  getPodpingNodes: (...args: unknown[]) => mockGet(...args),
  updatePodpingNodes: (...args: unknown[]) => mockUpdate(...args),
  resetPodpingNodes: (...args: unknown[]) => mockReset(...args),
}));

const defaults = ['https://one.example', 'https://two.example'];

function renderSection(enabled = true) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PodpingServersSection enabled={enabled} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue({ nodes: defaults, defaults });
  mockUpdate.mockImplementation(async (nodes: string[]) => {
    mockGet.mockResolvedValue({ nodes, defaults });
    return { nodes, defaults };
  });
  mockReset.mockResolvedValue({ nodes: defaults, defaults } satisfies PodpingNodes);
});

describe('PodpingServersSection', () => {
  it('renders the configured endpoints and keeps observed hosts separate', async () => {
    renderSection();

    expect(await screen.findByDisplayValue('https://one.example')).toBeDefined();
    expect(screen.getByText(/Observed feed hosts remain separate/)).toBeDefined();
  });

  it('edits and saves an existing endpoint before any other action', async () => {
    const user = userEvent.setup();
    renderSection();

    const first = await screen.findByLabelText('Hive RPC node 1');
    await user.clear(first);
    await user.type(first, 'https://edited.example');
    await user.click(screen.getByRole('button', { name: 'Save nodes' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith([
      'https://edited.example', 'https://two.example',
    ], expect.any(Object)));
  });

  it('disables URL edits until a pending save has finished', async () => {
    const user = userEvent.setup();
    let finishSave!: (result: PodpingNodes) => void;
    mockUpdate.mockReturnValueOnce(new Promise<PodpingNodes>((resolve) => {
      finishSave = resolve;
    }));
    renderSection();

    const first = await screen.findByLabelText('Hive RPC node 1');
    await user.clear(first);
    await user.type(first, 'https://edited.example');
    await user.click(screen.getByRole('button', { name: 'Save nodes' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledOnce());
    expect((first as HTMLInputElement).disabled).toBe(true);
    expect((first as HTMLInputElement).value).toBe('https://edited.example');

    await act(async () => {
      finishSave({ nodes: ['https://edited.example', 'https://two.example'], defaults });
    });
    expect(await screen.findByDisplayValue('https://edited.example')).toBeDefined();
  });

  it('supports adding, ordering and saving endpoints', async () => {
    const user = userEvent.setup();
    renderSection();

    const first = await screen.findByLabelText('Hive RPC node 1');
    await user.click(screen.getByRole('button', { name: 'Move Hive RPC node 1 down' }));
    await user.click(screen.getByRole('button', { name: 'Add node' }));
    await user.type(screen.getByLabelText('Hive RPC node 3'), 'https://three.example');
    const saveButton = screen.getByRole('button', { name: 'Save nodes' });
    await user.click(saveButton);

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith([
      'https://two.example', 'https://one.example', 'https://three.example',
    ], expect.any(Object)));
    expect((first as HTMLInputElement).value).toBe('https://two.example');
  });

  it('removes an endpoint and resets the list to defaults', async () => {
    const user = userEvent.setup();
    renderSection();

    await screen.findByLabelText('Hive RPC node 1');
    const row = screen.getByLabelText('Hive RPC node 2').parentElement;
    await user.click(within(row as HTMLElement).getByRole('button', { name: 'Remove Hive RPC node 2' }));
    await user.click(screen.getByRole('button', { name: 'Save nodes' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      ['https://one.example'], expect.any(Object)));

    await user.click(screen.getByRole('button', { name: 'Reset defaults' }));
    await waitFor(() => expect(mockReset).toHaveBeenCalledOnce());
    expect(await screen.findByDisplayValue('https://two.example')).toBeDefined();
  });
});
