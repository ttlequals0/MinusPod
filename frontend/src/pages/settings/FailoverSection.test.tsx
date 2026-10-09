import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FailoverSection from './FailoverSection';
import type { FailoverOverview } from '../../api/failover';
import type { ClaudeModel } from '../../api/types';
import { makeFailoverProbe, makeFailoverTarget } from '../../test/failover';

type Props = Parameters<typeof FailoverSection>[0];

const models: ClaudeModel[] = [{ id: 'gpt-5', name: 'GPT-5' }];

const unprobed = makeFailoverProbe();
const idle = makeFailoverTarget();
const unconfigured = makeFailoverTarget({ configured: false });

function makeOverview(overrides: Partial<FailoverOverview> = {}): FailoverOverview {
  return {
    targets: {
      'llm-a': idle, 'llm-b': idle, transcriber: idle,
    },
    probes: {
      'llm-a': unprobed, 'llm-b': unprobed, 'llm-failover': unprobed,
      transcriber: unprobed, 'transcriber-failover': unprobed,
    },
    policy: { probeIntervalMinutes: 5, recoveryProbes: 3 },
    events: [],
    ...overrides,
  };
}

const baseLlm: Props['llm'] = {
  enabled: false, provider: '', baseUrl: '', timeoutSeconds: null, maxRetries: null,
  detectionModel: '', reviewModel: '', verificationModel: '', chaptersModel: '',
  apiKeyConfigured: false,
};

const baseWhisper: Props['whisper'] = {
  enabled: false, backend: 'openai-api', model: '', apiBaseUrl: '', apiModel: 'whisper-1',
  apiTimeoutSeconds: 600, maxAttempts: null, language: '', apiKeyConfigured: false,
};

function props(overrides: Partial<Props> = {}): Props {
  return {
    overview: makeOverview(),
    overviewLoading: false,
    onTrigger: vi.fn(),
    onCancel: vi.fn(),
    onProbeNow: vi.fn(),
    actionPending: false,
    probePending: false,
    actionError: null,
    probeIntervalMinutes: 5,
    onProbeIntervalChange: vi.fn(),
    recoveryProbes: 3,
    onRecoveryProbesChange: vi.fn(),
    llm: baseLlm,
    onLlmChange: vi.fn(),
    onLlmApiKeySave: vi.fn().mockResolvedValue(undefined),
    onLlmApiKeyClear: vi.fn().mockResolvedValue(undefined),
    failoverCatalog: { models, isLoading: false, isError: false },
    whisper: baseWhisper,
    activeWhisperMaxAttempts: 2,
    onWhisperChange: vi.fn(),
    onWhisperApiKeySave: vi.fn().mockResolvedValue(undefined),
    onWhisperApiKeyClear: vi.fn().mockResolvedValue(undefined),
    skipFlacCompression: false,
    cryptoReady: true,
    ...overrides,
  };
}

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function sectionWith(overrides: Partial<Props> = {}) {
  return (
    <QueryClientProvider client={qc}>
      <FailoverSection {...props(overrides)} />
    </QueryClientProvider>
  );
}

function renderSection(overrides: Partial<Props> = {}) {
  return render(sectionWith(overrides));
}

async function openSection() {
  const header = screen.getByRole('button', { name: /^Failover/ });
  if (header.getAttribute('aria-expanded') !== 'true') await userEvent.click(header);
}

beforeEach(() => {
  localStorage.clear();
});

describe('FailoverSection status rows', () => {
  it('shows one status row per target with trigger or cancel', async () => {
    const onTrigger = vi.fn(); const onCancel = vi.fn();
    renderSection({ onTrigger, onCancel, overview: makeOverview({
      targets: {
        'llm-a': makeFailoverTarget({ active: true, source: 'probe', since: '2026-10-05T00:00:00Z', reason: 'HTTP 503' }),
        'llm-b': idle, transcriber: unconfigured } }) });
    await openSection();
    expect(screen.getByText('Provider A')).toBeDefined();
    expect(screen.getByText('Failed over')).toBeDefined();
    expect(screen.getByText(/HTTP 503/)).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel failover for Provider A' }));
    expect(onCancel).toHaveBeenCalledWith('llm-a');
    await userEvent.click(screen.getByRole('button', { name: 'Trigger failover for Provider B' }));
    expect(onTrigger).toHaveBeenCalledWith('llm-b');
    expect((screen.getByRole('button', { name: 'Trigger failover for Transcriber' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Failover not configured')).toBeDefined();
  });

  it('shows a disabled Provider B as off with no action', async () => {
    renderSection({ overview: makeOverview({
      targets: { 'llm-a': idle, 'llm-b': makeFailoverTarget({ enabled: false }), transcriber: idle } }) });
    await openSection();
    expect(screen.getByText('Provider B is off')).toBeDefined();
    expect(screen.getByText('Off')).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Trigger failover for Provider B' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Trigger failover for Provider A' })).toBeDefined();
  });

  it('badges a probed target by reachability', async () => {
    renderSection({ overview: makeOverview({
      probes: {
        'llm-a': makeFailoverProbe({ reachable: true, checkedAt: '2026-10-05T00:00:00Z' }),
        'llm-b': makeFailoverProbe({ reachable: false, checkedAt: '2026-10-05T00:00:00Z' }),
        'llm-failover': unprobed, transcriber: unprobed, 'transcriber-failover': unprobed,
      } }) });
    await openSection();
    expect(screen.getByText('Healthy')).toBeDefined();
    expect(screen.getByText('Unreachable')).toBeDefined();
    // Transcriber plus the two standby rows are unprobed.
    expect(screen.getAllByText('Unprobed')).toHaveLength(3);
  });

  it('shows the standby accounts as rows without actions', async () => {
    renderSection({ overview: makeOverview({
      targets: { 'llm-a': idle, 'llm-b': idle, transcriber: unconfigured },
      probes: {
        'llm-a': unprobed, 'llm-b': unprobed, transcriber: unprobed,
        'llm-failover': makeFailoverProbe({ reachable: false, detail: 'Connection refused', checkedAt: '2026-10-05T00:00:00Z' }),
        'transcriber-failover': unprobed,
      } }) });
    await openSection();
    const rows = within(screen.getByRole('list', { name: 'Failover status' })).getAllByRole('listitem');
    expect(rows.map((r) => r.querySelector('span')?.textContent)).toEqual(
      ['Provider A', 'Provider B', 'Transcriber', 'LLM failover', 'Transcriber failover']);
    const [llmStandby, whisperStandby] = rows.slice(3);
    expect(within(llmStandby).getByText('Unreachable')).toBeDefined();
    expect(within(llmStandby).getByText(/Connection refused/)).toBeDefined();
    expect(within(whisperStandby).getByText('Not configured')).toBeDefined();
    expect(within(llmStandby).queryByRole('button')).toBeNull();
    expect(within(whisperStandby).queryByRole('button')).toBeNull();
  });

  it('probe now calls back', async () => {
    const onProbeNow = vi.fn();
    renderSection({ onProbeNow });
    await openSection();
    await userEvent.click(screen.getByRole('button', { name: 'Probe now' }));
    expect(onProbeNow).toHaveBeenCalled();
  });

  it('lists recent events', async () => {
    renderSection({ overview: makeOverview({ events: [
      { id: 1, target: 'llm-a', action: 'trigger', source: 'auto', reason: 'Connection refused', createdAt: '2026-10-05T00:00:00Z' },
    ] }) });
    await openSection();
    expect(screen.getByText('Recent events (1)')).toBeDefined();
    expect(screen.getByText(/Connection refused/)).toBeDefined();
  });
});

describe('FailoverSection LLM failover', () => {
  it('hides llm failover fields until enabled and shows four model pickers when on', async () => {
    const { rerender } = renderSection({ llm: { ...baseLlm, enabled: false } });
    await openSection();
    expect(screen.queryByLabelText('Failover provider type')).toBeNull();
    rerender(sectionWith({ llm: { ...baseLlm, enabled: true, provider: 'openai-compatible' } }));
    expect(screen.getByLabelText('Failover provider type')).toBeDefined();
    for (const label of ['Detection model', 'Review model', 'Verification model', 'Chapters model']) {
      expect(screen.getByLabelText(label)).toBeDefined();
    }
    expect(screen.getAllByText('Same as detection').length).toBeGreaterThan(0);
  });

  it('shows the base URL only for configurable provider types', async () => {
    const { rerender } = renderSection({ llm: { ...baseLlm, enabled: true, provider: 'anthropic' } });
    await openSection();
    expect(screen.queryByLabelText('Failover base URL')).toBeNull();
    rerender(sectionWith({ llm: { ...baseLlm, enabled: true, provider: 'ollama' } }));
    expect(screen.getByLabelText('Failover base URL')).toBeDefined();
  });

  it('clears the base URL and models when the provider type changes', async () => {
    const onLlmChange = vi.fn();
    renderSection({ onLlmChange, llm: { ...baseLlm, enabled: true, provider: 'ollama', baseUrl: 'http://example.com/v1', detectionModel: 'gpt-5' } });
    await openSection();
    await userEvent.selectOptions(screen.getByLabelText('Failover provider type'), 'anthropic');
    expect(onLlmChange).toHaveBeenCalledWith({
      provider: 'anthropic', baseUrl: '',
      detectionModel: '', reviewModel: '', verificationModel: '', chaptersModel: '',
    });
  });

  it('clears failover models when the base URL changes', async () => {
    const onLlmChange = vi.fn();
    renderSection({ onLlmChange, llm: {
      ...baseLlm, enabled: true, provider: 'ollama', baseUrl: 'http://old.example/v1',
      detectionModel: 'old-detection', verificationModel: 'old-verification',
    } });
    await openSection();

    fireEvent.change(screen.getByLabelText('Failover base URL'), {
      target: { value: 'http://new.example/v1' },
    });

    expect(onLlmChange).toHaveBeenCalledWith({
      baseUrl: 'http://new.example/v1',
      detectionModel: '', reviewModel: '', verificationModel: '', chaptersModel: '',
    });
  });

  it('sends blank timeout and retries as null', async () => {
    const onLlmChange = vi.fn();
    renderSection({ onLlmChange, llm: { ...baseLlm, enabled: true, provider: 'anthropic', timeoutSeconds: 90 } });
    await openSection();
    await userEvent.clear(screen.getByLabelText('Failover request timeout (seconds)'));
    expect(onLlmChange).toHaveBeenLastCalledWith({ timeoutSeconds: null });
    expect((screen.getByLabelText('Failover max retries') as HTMLInputElement).placeholder).toBe('3');
  });
});

describe('FailoverSection transcription failover', () => {
  it('transcriber failover switches fields by backend', async () => {
    renderSection({ whisper: { ...baseWhisper, enabled: true, backend: 'local' } });
    await openSection();
    expect(screen.getByLabelText('Local model')).toBeDefined();
    expect(screen.queryByLabelText('API base URL')).toBeNull();
  });

  it('shows the API fields for the API backend', async () => {
    renderSection({ whisper: { ...baseWhisper, enabled: true, backend: 'openai-api' } });
    await openSection();
    expect(screen.getByLabelText('API base URL')).toBeDefined();
    expect(screen.getByLabelText('API model')).toBeDefined();
    expect(screen.getByLabelText('API timeout (seconds)')).toBeDefined();
    expect(screen.getByLabelText('Max upload attempts')).toBeDefined();
    expect(screen.queryByLabelText('Local model')).toBeNull();
    expect(screen.getByLabelText('Language')).toBeDefined();
  });

  it('edits standby attempts and clearing restores dynamic inheritance', async () => {
    const onWhisperChange = vi.fn();
    const { rerender } = renderSection({
      onWhisperChange, activeWhisperMaxAttempts: 3,
      whisper: { ...baseWhisper, enabled: true, maxAttempts: 5 },
    });
    await openSection();
    const field = screen.getByLabelText('Max upload attempts') as HTMLInputElement;
    expect(field.value).toBe('5');
    await userEvent.clear(field);
    expect(onWhisperChange).toHaveBeenLastCalledWith({ maxAttempts: null });
    await userEvent.type(field, '4');
    expect(onWhisperChange).toHaveBeenLastCalledWith({ maxAttempts: 4 });
    field.blur();
    rerender(sectionWith({
      activeWhisperMaxAttempts: 7,
      whisper: { ...baseWhisper, enabled: true, maxAttempts: null },
    }));
    expect(field.value).toBe('');
    expect(field.placeholder).toBe('7');
  });
});
