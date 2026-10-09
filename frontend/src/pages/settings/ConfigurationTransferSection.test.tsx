import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ConfigurationTransferSection from './ConfigurationTransferSection';

const mocks = vi.hoisted(() => ({
  apply: vi.fn(),
  download: vi.fn(),
  preview: vi.fn(),
}));

vi.mock('../../api/configTransfer', () => ({
  applyConfigImport: (...args: unknown[]) => mocks.apply(...args),
  downloadRuntimeConfig: (...args: unknown[]) => mocks.download(...args),
  previewConfigImport: (...args: unknown[]) => mocks.preview(...args),
}));

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <ConfigurationTransferSection />
    </QueryClientProvider>,
  );
  return invalidate;
}

const document = {
  format: 'minuspod-runtime-config',
  formatVersion: 1,
  appVersion: '2.98.2',
  exportedAt: '2026-10-08T00:00:00Z',
  containsSecrets: true,
  settings: {},
  feeds: [{ slug: 'example-feed', feedType: 'subscribed', settings: { source_url: 'https://example.com/feed.xml' } }],
};

const preview = {
  previewToken: 'digest', scope: 'everything', settings: [],
  changedSettings: [{ kind: 'setting', key: 'openai_api_key', secret: true }],
  addedFeeds: ['example-feed'], updatedFeeds: [], skippedUnknownSettings: [],
  preservesTargetOnlyFeeds: true, warning: 'Contains credentials.',
  warnings: [], selectedFeedSlugs: ['example-feed'],
};

describe('ConfigurationTransferSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.preview.mockResolvedValue(preview);
    mocks.apply.mockResolvedValue({ message: 'Configuration imported', warnings: [] });
  });

  it('requires the credential warning acknowledgment before download', async () => {
    renderSection();
    const download = screen.getByRole('button', { name: 'Download Configuration JSON' });
    expect((download as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(download);
    await waitFor(() => expect(mocks.download).toHaveBeenCalledOnce());
  });

  it('previews selected JSON, masks credential values, applies and refreshes queries', async () => {
    const invalidate = renderSection();
    const upload = screen.getByLabelText('Import configuration JSON');
    const file = new File([JSON.stringify(document)], 'settings.json', { type: 'application/json' });
    await userEvent.upload(upload, file);
    await userEvent.click(screen.getByRole('button', { name: 'Preview Import' }));

    expect(await screen.findByText(/1 changed settings, 1 new feeds/)).toBeDefined();
    expect(screen.getByText('Setting: openai_api_key (credential)')).toBeDefined();
    expect(screen.getByText('New feed: example-feed')).toBeDefined();
    expect(screen.queryByText(/secret-value|api-key-value/)).toBeNull();
    expect(mocks.preview).toHaveBeenCalledWith({ document, scope: 'everything' });

    await userEvent.click(screen.getByRole('button', { name: 'Apply Import' }));
    await waitFor(() => expect(mocks.apply).toHaveBeenCalledWith({
      document, scope: 'everything', previewToken: 'digest',
    }));
    expect(invalidate).toHaveBeenCalledOnce();
    expect((await screen.findByRole('status')).textContent).toContain('Configuration imported.');
  });

  it('keeps the selected file when preview fails', async () => {
    mocks.preview.mockRejectedValue(new Error('Preview rejected'));
    renderSection();
    const file = new File([JSON.stringify(document)], 'settings.json', { type: 'application/json' });
    await userEvent.upload(screen.getByLabelText('Import configuration JSON'), file);
    await userEvent.click(screen.getByRole('button', { name: 'Preview Import' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Preview rejected');
    expect(screen.getByText('Selected: settings.json')).toBeDefined();
  });

  it('locks selections while previewing and applying, then shows refresh warnings', async () => {
    let finishPreview!: (value: typeof preview) => void;
    let finishApply!: (value: { warnings: string[] }) => void;
    mocks.preview.mockImplementation(() => new Promise((resolve) => { finishPreview = resolve; }));
    mocks.apply.mockImplementation(() => new Promise((resolve) => { finishApply = resolve; }));
    renderSection();
    const fileInput = screen.getByLabelText('Import configuration JSON') as HTMLInputElement;
    const scope = screen.getByLabelText('Import scope') as HTMLSelectElement;
    await userEvent.upload(fileInput, new File([JSON.stringify(document)], 'settings.json', { type: 'application/json' }));
    await userEvent.click(screen.getByRole('button', { name: 'Preview Import' }));
    expect(fileInput.disabled).toBe(true);
    expect(scope.disabled).toBe(true);
    await act(async () => { finishPreview(preview); });
    expect(fileInput.disabled).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Apply Import' }));
    expect(fileInput.disabled).toBe(true);
    expect(scope.disabled).toBe(true);
    await act(async () => { finishApply({ warnings: ['Feed refresh needs another attempt.'] }); });
    expect(screen.getByText('Feed refresh needs another attempt.')).toBeDefined();
    expect(scope.disabled).toBe(false);
  });

  it('keeps the newest file when a previous file finishes reading later', async () => {
    let finishOld!: (value: string) => void;
    renderSection();
    const oldFile = new File(['{}'], 'old.json', { type: 'application/json' });
    Object.defineProperty(oldFile, 'text', { value: () => new Promise<string>((resolve) => { finishOld = resolve; }) });
    const input = screen.getByLabelText('Import configuration JSON');
    await userEvent.upload(input, oldFile);
    await userEvent.upload(input, new File([JSON.stringify(document)], 'new.json', { type: 'application/json' }));
    await act(async () => { finishOld(JSON.stringify({ ...document, feeds: [] })); });
    await userEvent.click(screen.getByRole('button', { name: 'Preview Import' }));
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledWith({ document, scope: 'everything' }));
    expect(screen.getByText('Selected: new.json')).toBeDefined();
  });

  it('rejects malformed feed objects and oversized files before preview', async () => {
    renderSection();
    const input = screen.getByLabelText('Import configuration JSON');
    await userEvent.upload(input, new File([JSON.stringify({ ...document, feeds: [{ slug: {} }] })], 'bad.json', { type: 'application/json' }));
    expect((await screen.findByRole('alert')).textContent).toContain('not a MinusPod');
    const oversized = new File(['{}'], 'large.json', { type: 'application/json' });
    Object.defineProperty(oversized, 'size', { value: 10 * 1024 * 1024 + 1 });
    const read = vi.fn();
    Object.defineProperty(oversized, 'text', { value: read });
    await userEvent.upload(input, oversized);
    expect((await screen.findByRole('alert')).textContent).toContain('10 MiB');
    expect(read).not.toHaveBeenCalled();
    expect(mocks.preview).not.toHaveBeenCalled();
  });
});
