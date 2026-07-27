/**
 * The operator has to be able to tell which file is in use, hear it, and know
 * what an upload will cost them before they commit to it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ReplacementAudioField from './ReplacementAudioField';
import type { ReplacementAudio } from '../../api/types';

const mockGet = vi.fn();
const mockUpload = vi.fn();
const mockRevert = vi.fn();

vi.mock('../../api/settings', () => ({
  getReplacementAudio: (...args: unknown[]) => mockGet(...args),
  uploadReplacementAudio: (...args: unknown[]) => mockUpload(...args),
  revertReplacementAudio: (...args: unknown[]) => mockRevert(...args),
}));

function makeAudio(overrides: Partial<ReplacementAudio> = {}): ReplacementAudio {
  return {
    source: 'default',
    canRevert: false,
    exists: true,
    sizeBytes: 34560,
    updatedAt: 1765811226,
    durationSeconds: 1.08,
    channels: 2,
    sampleRateHz: 48000,
    ...overrides,
  };
}

function renderField() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReplacementAudioField />
    </QueryClientProvider>,
  );
}

// The picker is hidden behind the Upload button, so there is no accessible
// name to query it by.
function fileInput(container: HTMLElement): HTMLInputElement {
  const el = container.querySelector('input[type=file]');
  if (!el) throw new Error('file input not found');
  return el as HTMLInputElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue(makeAudio());
});

describe('ReplacementAudioField', () => {
  it('describes the file in use', async () => {
    renderField();

    expect(await screen.findByText(/1\.08s · stereo · 48\.0 kHz/)).toBeDefined();
  });

  it('labels the shipped file as the default and offers no revert', async () => {
    renderField();

    expect(await screen.findByText('Default')).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Use the default' })).toBeNull();
  });

  it('offers a revert once a file has been uploaded', async () => {
    mockGet.mockResolvedValue(makeAudio({ source: 'uploaded', canRevert: true }));
    renderField();

    expect(await screen.findByText('Your file')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Use the default' })).toBeDefined();
  });

  it('warns that a mono upload can make an episode mono', async () => {
    mockGet.mockResolvedValue(makeAudio({ source: 'uploaded', canRevert: true, channels: 1 }));
    renderField();

    expect(await screen.findByText(/This file is mono/)).toBeDefined();
  });

  it('does not warn about mono for the shipped default', async () => {
    mockGet.mockResolvedValue(makeAudio({ channels: 1 }));
    renderField();

    await screen.findByText('Default');
    expect(screen.queryByText(/This file is mono/)).toBeNull();
  });

  it('says cuts render as silence when no file is installed', async () => {
    mockGet.mockResolvedValue(makeAudio({ exists: false, durationSeconds: null }));
    renderField();

    expect(await screen.findByText(/cut ads render as silence/)).toBeDefined();
  });

  it('disables play when there is no file to play', async () => {
    mockGet.mockResolvedValue(makeAudio({ exists: false }));
    renderField();

    await screen.findByText(/cut ads render as silence/);
    expect((screen.getByRole('button', { name: /Play the replacement audio/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the server rejection verbatim', async () => {
    mockUpload.mockRejectedValue(new Error('That file is 45.0 seconds. The limit is 30.'));
    const { container } = renderField();
    await screen.findByText('Default');

    const file = new File(['x'], 'long.mp3', { type: 'audio/mpeg' });
    await userEvent.upload(fileInput(container), file);

    expect(await screen.findByText(/That file is 45\.0 seconds/)).toBeDefined();
  });

  it('sends the picked file and refetches on success', async () => {
    mockUpload.mockResolvedValue(makeAudio({ source: 'uploaded', canRevert: true }));
    const { container } = renderField();
    await screen.findByText('Default');

    const file = new File(['x'], 'beep.wav', { type: 'audio/wav' });
    await userEvent.upload(fileInput(container), file);

    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith(file));
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('reverts and refetches', async () => {
    mockGet.mockResolvedValue(makeAudio({ source: 'uploaded', canRevert: true }));
    mockRevert.mockResolvedValue(makeAudio());
    renderField();

    await userEvent.click(await screen.findByRole('button', { name: 'Use the default' }));

    await waitFor(() => expect(mockRevert).toHaveBeenCalled());
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('states the upload limits before the operator picks a file', async () => {
    renderField();

    expect(await screen.findByText(/Up to 5 MB and 30 seconds/)).toBeDefined();
  });
});
