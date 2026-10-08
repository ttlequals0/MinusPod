/**
 * Tests for the Global Defaults settings section, including the feed
 * refresh interval field and the Podping notifications toggle added
 * alongside the podping-listener feature.
 *
 * Segment actions (per-category matrix + show-segments default) moved to
 * their own card, SegmentActionsSection; see SegmentActionsSection.test.tsx.
 */
import { useState } from 'react';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import GlobalDefaultsSection from './GlobalDefaultsSection';
import type { EpisodeLogLevel, LowAdYieldAction } from '../../api/types';

const { mockGetPodpingNodes } = vi.hoisted(() => ({
  mockGetPodpingNodes: vi.fn(),
}));

vi.mock('../../api/podping', () => ({
  podpingNodesQueryKey: ['podping', 'nodes'],
  getPodpingNodes: (...args: unknown[]) => mockGetPodpingNodes(...args),
  updatePodpingNodes: async (nodes: string[]) => ({ nodes, defaults: nodes }),
  resetPodpingNodes: async () => ({
    nodes: ['https://one.example'], defaults: ['https://one.example'],
  }),
}));

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

function Harness({ onCommit }: { onCommit: (minutes: number) => void }) {
  const [minutes, setMinutes] = useState(15);
  return (
    <>
      <GlobalDefaultsSection
        autoProcessEnabled={false}
        onAutoProcessEnabledChange={() => {}}
        rssRefreshIntervalMinutes={minutes}
        onRssRefreshIntervalMinutesChange={setMinutes}
        podpingEnabled={false}
        onPodpingEnabledChange={() => {}}
        maxFeedEpisodes={10}
        onMaxFeedEpisodesChange={() => {}}
        onlyExposeProcessedDefault={false}
        onOnlyExposeProcessedDefaultChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
        skipSecondPass={false}
        onSkipSecondPassChange={() => {}}
        differentialFetchMode="auto"
        onDifferentialFetchModeChange={() => {}}
      />
      <button onClick={() => onCommit(minutes)}>Commit</button>
    </>
  );
}

interface PodpingState {
  podpingEnabled: boolean;
}

function PodpingHarness({ onCommit }: { onCommit: (payload: PodpingState) => void }) {
  const [podpingEnabled, setPodpingEnabled] = useState(false);
  return (
    <>
      <GlobalDefaultsSection
        autoProcessEnabled={false}
        onAutoProcessEnabledChange={() => {}}
        rssRefreshIntervalMinutes={15}
        onRssRefreshIntervalMinutesChange={() => {}}
        podpingEnabled={podpingEnabled}
        onPodpingEnabledChange={setPodpingEnabled}
        maxFeedEpisodes={10}
        onMaxFeedEpisodesChange={() => {}}
        onlyExposeProcessedDefault={false}
        onOnlyExposeProcessedDefaultChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
        skipSecondPass={false}
        onSkipSecondPassChange={() => {}}
        differentialFetchMode="auto"
        onDifferentialFetchModeChange={() => {}}
      />
      <button onClick={() => onCommit({ podpingEnabled })}>Commit</button>
    </>
  );
}

beforeEach(() => {
  localStorage.setItem('settings-section-global-defaults', 'true');
  localStorage.removeItem('settings-section-podping-servers');
  mockGetPodpingNodes.mockReset();
  mockGetPodpingNodes.mockResolvedValue({
    nodes: ['https://one.example'], defaults: ['https://one.example'],
  });
});

describe('GlobalDefaultsSection: Podping notifications toggle', () => {
  it('renders off by default', () => {
    render(<PodpingHarness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Podping notifications' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByLabelText('Hive RPC node 1')).toBeNull();
    expect(mockGetPodpingNodes).not.toHaveBeenCalled();
  });

  it('commits { podpingEnabled: true } after switching on', async () => {
    let committed: PodpingState | null = null;
    render(<PodpingHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Podping notifications' }));
    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({ podpingEnabled: true });
  });

  it('commits { podpingEnabled: false } after switching on then off again', async () => {
    let committed: PodpingState | null = null;
    render(<PodpingHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    const toggle = screen.getByRole('switch', { name: 'Podping notifications' });
    await user.click(toggle);
    await user.click(toggle);
    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({ podpingEnabled: false });
  });

  it('shows the nested server editor only while enabled and preserves its draft', async () => {
    const user = userEvent.setup();
    render(<PodpingHarness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Podping notifications' });

    await user.click(toggle);
    const input = await screen.findByLabelText('Hive RPC node 1');
    expect(mockGetPodpingNodes).toHaveBeenCalledOnce();
    await user.clear(input);
    await user.type(input, 'https://draft.example');

    await user.click(toggle);
    expect(screen.queryByLabelText('Hive RPC node 1')).toBeNull();
    await user.click(toggle);

    expect(await screen.findByDisplayValue('https://draft.example')).toBeDefined();
    await waitFor(() => expect(mockGetPodpingNodes).toHaveBeenCalledOnce());
  });
});

describe('GlobalDefaultsSection: no Segment actions details block', () => {
  it('does not render a Segment actions details element (moved to SegmentActionsSection)', () => {
    const { container } = render(<Harness onCommit={() => {}} />);
    expect(screen.queryByText('Segment actions')).toBeNull();
    expect(container.querySelector('details')).toBeNull();
  });
});

interface LowAdYieldState {
  lowAdYieldAction: LowAdYieldAction;
}

function LowAdYieldHarness({ onCommit }: { onCommit: (payload: LowAdYieldState) => void }) {
  const [lowAdYieldAction, setLowAdYieldAction] = useState<LowAdYieldAction>('nothing');
  return (
    <>
      <GlobalDefaultsSection
        autoProcessEnabled={false}
        onAutoProcessEnabledChange={() => {}}
        rssRefreshIntervalMinutes={15}
        onRssRefreshIntervalMinutesChange={() => {}}
        podpingEnabled={false}
        onPodpingEnabledChange={() => {}}
        maxFeedEpisodes={10}
        onMaxFeedEpisodesChange={() => {}}
        onlyExposeProcessedDefault={false}
        onOnlyExposeProcessedDefaultChange={() => {}}
        lowAdYieldAction={lowAdYieldAction}
        onLowAdYieldActionChange={setLowAdYieldAction}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
        skipSecondPass={false}
        onSkipSecondPassChange={() => {}}
        differentialFetchMode="auto"
        onDifferentialFetchModeChange={() => {}}
      />
      <button onClick={() => onCommit({ lowAdYieldAction })}>Commit</button>
    </>
  );
}

describe('GlobalDefaultsSection: low ad yield action', () => {
  it('defaults to Do nothing', () => {
    render(<LowAdYieldHarness onCommit={() => {}} />);
    const select = screen.getByLabelText('When an episode detects fewer ads than usual') as HTMLSelectElement;
    expect(select.value).toBe('nothing');
  });

  it('offers all four actions', () => {
    render(<LowAdYieldHarness onCommit={() => {}} />);
    const select = screen.getByLabelText('When an episode detects fewer ads than usual') as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(
      ['nothing', 'redetect', 'reprocess', 'full']);
  });

  it('commits the chosen action', async () => {
    let committed: LowAdYieldState | null = null;
    render(<LowAdYieldHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    await user.selectOptions(
      screen.getByLabelText('When an episode detects fewer ads than usual'), 'full');
    await user.click(screen.getByRole('button', { name: 'Commit' }));
    expect(committed!.lowAdYieldAction).toBe('full');
  });
});

interface EpisodeLogState {
  retentionDays: number;
  level: EpisodeLogLevel;
}

function EpisodeLogHarness({ onCommit }: { onCommit: (payload: EpisodeLogState) => void }) {
  const [retentionDays, setRetentionDays] = useState(30);
  const [level, setLevel] = useState<EpisodeLogLevel>('debug');
  return (
    <>
      <GlobalDefaultsSection
        autoProcessEnabled={false}
        onAutoProcessEnabledChange={() => {}}
        rssRefreshIntervalMinutes={15}
        onRssRefreshIntervalMinutesChange={() => {}}
        podpingEnabled={false}
        onPodpingEnabledChange={() => {}}
        maxFeedEpisodes={10}
        onMaxFeedEpisodesChange={() => {}}
        onlyExposeProcessedDefault={false}
        onOnlyExposeProcessedDefaultChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={retentionDays}
        onEpisodeLogRetentionDaysChange={setRetentionDays}
        episodeLogLevel={level}
        onEpisodeLogLevelChange={setLevel}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
        skipSecondPass={false}
        onSkipSecondPassChange={() => {}}
        differentialFetchMode="auto"
        onDifferentialFetchModeChange={() => {}}
      />
      <button onClick={() => onCommit({ retentionDays, level })}>Commit</button>
    </>
  );
}

describe('GlobalDefaultsSection: episode run logs', () => {
  it('shows the current retention and level', () => {
    render(<EpisodeLogHarness onCommit={() => {}} />);
    expect((screen.getByLabelText('Keep episode run logs for') as HTMLInputElement).value).toBe('30');
    expect((screen.getByLabelText('Detail kept in a run log') as HTMLSelectElement).value).toBe('debug');
  });

  it('commits a new retention value', async () => {
    let committed: EpisodeLogState | null = null;
    render(<EpisodeLogHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    const input = screen.getByLabelText('Keep episode run logs for');
    await user.clear(input);
    await user.type(input, '7');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Commit' }));
    expect(committed!.retentionDays).toBe(7);
  });

  it('commits the chosen level', async () => {
    let committed: EpisodeLogState | null = null;
    render(<EpisodeLogHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText('Detail kept in a run log'), 'info');
    await user.click(screen.getByRole('button', { name: 'Commit' }));
    expect(committed!.level).toBe('info');
  });
});

interface TextRecurrenceHintsState {
  textRecurrenceHints: boolean;
}

function TextRecurrenceHintsHarness({ onCommit }: { onCommit: (payload: TextRecurrenceHintsState) => void }) {
  const [textRecurrenceHints, setTextRecurrenceHints] = useState(false);
  return (
    <>
      <GlobalDefaultsSection
        autoProcessEnabled={false}
        onAutoProcessEnabledChange={() => {}}
        rssRefreshIntervalMinutes={15}
        onRssRefreshIntervalMinutesChange={() => {}}
        podpingEnabled={false}
        onPodpingEnabledChange={() => {}}
        maxFeedEpisodes={10}
        onMaxFeedEpisodesChange={() => {}}
        onlyExposeProcessedDefault={false}
        onOnlyExposeProcessedDefaultChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={textRecurrenceHints}
        onTextRecurrenceHintsChange={setTextRecurrenceHints}
        skipSecondPass={false}
        onSkipSecondPassChange={() => {}}
        differentialFetchMode="auto"
        onDifferentialFetchModeChange={() => {}}
      />
      <button onClick={() => onCommit({ textRecurrenceHints })}>Commit</button>
    </>
  );
}

describe('GlobalDefaultsSection: Text recurrence hints toggle', () => {
  it('renders off by default', () => {
    render(<TextRecurrenceHintsHarness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Text recurrence hints' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('commits { textRecurrenceHints: true } after switching on', async () => {
    let committed: TextRecurrenceHintsState | null = null;
    render(<TextRecurrenceHintsHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Text recurrence hints' }));
    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({ textRecurrenceHints: true });
  });
});
