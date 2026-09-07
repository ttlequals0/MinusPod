import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TranscriptionSection from './TranscriptionSection';
import { getWhisperCapacity } from '../../api/settings';

vi.mock('../../api/settings', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/settings')>()),
  getWhisperCapacity: vi.fn().mockResolvedValue({
    enabled: true, backend: 'openai-api', active: true, inactiveReason: null,
    capacity: 3, inFlight: 0, transcribingEpisodes: 0,
    maxEpisodes: { configured: 2, effective: 2 },
    chunkWorkers: { configured: 4, effective: 3 },
    worstCaseInFlight: 8, exceedsCapacity: true, leader: true,
    health: { available: false },
  }),
}));

function renderSection(
  overrides: Partial<React.ComponentProps<typeof TranscriptionSection>> = {},
  { open = true }: { open?: boolean } = {},
) {
  const noop = () => {};
  const props = {
    whisperModel: 'small', whisperModels: [], onWhisperModelChange: noop,
    whisperBackend: 'openai-api', onWhisperBackendChange: noop,
    apiConfig: { baseUrl: 'https://whisper.example.com/v1', model: 'whisper-1' }, onApiConfigChange: noop,
    providersState: null, onProviderKeySave: noop, onProviderKeyClear: noop, onProviderKeyTest: noop,
    onConnectionTest: noop, whisperLanguage: 'en', onWhisperLanguageChange: noop,
    whisperComputeType: 'auto', onWhisperComputeTypeChange: noop,
    transcribeMaxChunkSeconds: 600, onTranscribeMaxChunkSecondsChange: noop,
    transcribeConcurrentChunks: 4, onTranscribeConcurrentChunksChange: noop,
    transcribeChunkOverlapSeconds: 30, onTranscribeChunkOverlapSecondsChange: noop,
    whisperApiTimeoutSeconds: 600, onWhisperApiTimeoutSecondsChange: noop,
    skipFlacCompression: false, onSkipFlacCompressionChange: noop,
    softTimeoutMinutes: 60, hardTimeoutMinutes: 120, softMinMinutes: 5, hardMaxMinutes: 1440,
    onSoftTimeoutChange: noop, onHardTimeoutChange: noop,
    onTimeoutsSave: noop, timeoutsSaveIsPending: false, timeoutsSaveIsSuccess: false, timeoutsError: null,
    whisperPoolEnabled: true, onWhisperPoolEnabledChange: noop,
    whisperPoolMaxRequests: 3, onWhisperPoolMaxRequestsChange: noop,
    whisperPoolMaxEpisodes: 2, onWhisperPoolMaxEpisodesChange: noop,
    ...overrides,
  } as React.ComponentProps<typeof TranscriptionSection>;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<QueryClientProvider client={client}><TranscriptionSection {...props} /></QueryClientProvider>);
  // Section starts collapsed; the capacity poll only runs once visible, so
  // open it for cases that need the query to fire. Content itself stays in
  // the DOM either way (search must be able to match it while collapsed).
  if (open) {
    fireEvent.click(screen.getByRole('button', { name: 'Transcription' }));
  }
  return result;
}

describe('TranscriptionSection whisper pool', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the toggle and both dials for the remote backend', async () => {
    renderSection();
    expect(screen.getByRole('switch', { name: 'Whisper pool toggle' })).toBeTruthy();
    expect((screen.getByLabelText('Max requests to backend:') as HTMLInputElement).value).toBe('3');
    expect((screen.getByLabelText('Episodes at once:') as HTMLInputElement).value).toBe('2');
    expect(await screen.findByText(/Up to 8 requests in flight against a cap of 3/)).toBeTruthy();
  });

  it('disables the dials while the toggle is off', () => {
    renderSection({ whisperPoolEnabled: false });
    expect((screen.getByLabelText('Max requests to backend:') as HTMLInputElement).disabled).toBe(true);
  });

  it('hides the block on the local backend', () => {
    renderSection({ whisperBackend: 'local' });
    expect(screen.queryByRole('switch', { name: 'Whisper pool toggle' })).toBeNull();
  });

  it('omits the "Currently" sentence on a non-leader worker', async () => {
    vi.mocked(getWhisperCapacity).mockResolvedValueOnce({
      enabled: true, backend: 'openai-api', active: true, inactiveReason: null,
      capacity: 3, inFlight: 0, transcribingEpisodes: 0,
      maxEpisodes: { configured: 2, effective: 2 },
      chunkWorkers: { configured: 4, effective: 3 },
      worstCaseInFlight: 8, exceedsCapacity: true, leader: false,
      health: { available: false },
    });
    renderSection();
    expect(await screen.findByText(/Up to 8 requests in flight against a cap of 3/)).toBeTruthy();
    expect(screen.queryByText(/Currently/)).toBeNull();
  });

  it('keeps field labels in the DOM while collapsed, for settings search', () => {
    renderSection({}, { open: false });
    expect(screen.getByRole('button', { name: 'Transcription' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('Chunk overlap seconds:')).toBeTruthy();
  });

  it('shows the health suggestion when it differs from the configured cap', async () => {
    vi.mocked(getWhisperCapacity).mockResolvedValueOnce({
      enabled: true, backend: 'openai-api', active: true, inactiveReason: null,
      capacity: 4, inFlight: 0, transcribingEpisodes: 0,
      maxEpisodes: { configured: 2, effective: 2 },
      chunkWorkers: { configured: 4, effective: 3 },
      worstCaseInFlight: 8, exceedsCapacity: true, leader: false,
      health: {
        available: true,
        instances: [
          { instance: 'whisper-1', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
          { instance: 'whisper-2', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
          { instance: 'whisper-3', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
        ],
        suggested_max_requests: 3,
        mismatch: [],
      },
    });
    renderSection();
    expect(await screen.findByText(
      '3 instances reporting large-v3, 3 requests total. Your cap is 4.')).toBeTruthy();
  });

  it('shows a mismatch warning instead of the suggestion when instances disagree', async () => {
    vi.mocked(getWhisperCapacity).mockResolvedValueOnce({
      enabled: true, backend: 'openai-api', active: true, inactiveReason: null,
      capacity: 4, inFlight: 0, transcribingEpisodes: 0,
      maxEpisodes: { configured: 2, effective: 2 },
      chunkWorkers: { configured: 4, effective: 3 },
      worstCaseInFlight: 8, exceedsCapacity: true, leader: false,
      health: {
        available: true,
        instances: [
          { instance: 'whisper-1', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
          { instance: 'whisper-2', model: 'medium', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
        ],
        suggested_max_requests: 2,
        mismatch: ['model'],
      },
    });
    renderSection();
    expect(await screen.findByText('Instances disagree on model.')).toBeTruthy();
    expect(screen.queryByText(/requests total/)).toBeNull();
  });

  it('maps mismatch field names to human labels', async () => {
    vi.mocked(getWhisperCapacity).mockResolvedValueOnce({
      enabled: true, backend: 'openai-api', active: true, inactiveReason: null,
      capacity: 4, inFlight: 0, transcribingEpisodes: 0,
      maxEpisodes: { configured: 2, effective: 2 },
      chunkWorkers: { configured: 4, effective: 3 },
      worstCaseInFlight: 8, exceedsCapacity: true, leader: false,
      health: {
        available: true,
        instances: [
          { instance: 'whisper-1', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
          { instance: 'whisper-2', model: 'large-v3', device: 'cuda', compute_type: 'int8', batch_size: 16, max_concurrent: 1, vad_filter: true },
        ],
        suggested_max_requests: 2,
        mismatch: ['compute_type', 'device'],
      },
    });
    renderSection();
    expect(await screen.findByText('Instances disagree on compute type, device.')).toBeTruthy();
  });

  it('renders "at least N instances" when every sample was a new instance', async () => {
    vi.mocked(getWhisperCapacity).mockResolvedValueOnce({
      enabled: true, backend: 'openai-api', active: true, inactiveReason: null,
      capacity: 4, inFlight: 0, transcribingEpisodes: 0,
      maxEpisodes: { configured: 2, effective: 2 },
      chunkWorkers: { configured: 4, effective: 3 },
      worstCaseInFlight: 8, exceedsCapacity: true, leader: false,
      health: {
        available: true,
        instances: [
          { instance: 'whisper-1', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
          { instance: 'whisper-2', model: 'large-v3', device: 'cuda', compute_type: 'float16', batch_size: 16, max_concurrent: 1, vad_filter: true },
        ],
        suggested_max_requests: 2,
        mismatch: [],
        sampled_floor: true,
      },
    });
    renderSection();
    expect(await screen.findByText(
      'At least 2 instances reporting large-v3, 2 requests total. Your cap is 4.')).toBeTruthy();
  });
});
