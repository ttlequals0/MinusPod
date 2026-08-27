/**
 * Tests for the Global Defaults settings section, including the feed
 * refresh interval field and the Podping notifications toggle added
 * alongside the podping-listener feature.
 *
 * Segment actions (per-category matrix + show-segments default) moved to
 * their own card, SegmentActionsSection; see SegmentActionsSection.test.tsx.
 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GlobalDefaultsSection from './GlobalDefaultsSection';
import type { EpisodeLogLevel, LowAdYieldAction } from '../../api/types';

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
        processNewEpisodesFirst
        onProcessNewEpisodesFirstChange={() => {}}
        queueManualBoost={20}
        onQueueManualBoostChange={() => {}}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
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
        processNewEpisodesFirst
        onProcessNewEpisodesFirstChange={() => {}}
        queueManualBoost={20}
        onQueueManualBoostChange={() => {}}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
      />
      <button onClick={() => onCommit({ podpingEnabled })}>Commit</button>
    </>
  );
}

interface ProcessNewFirstState {
  processNewEpisodesFirst: boolean;
}

function ProcessNewFirstHarness({ onCommit }: { onCommit: (payload: ProcessNewFirstState) => void }) {
  const [processNewEpisodesFirst, setProcessNewEpisodesFirst] = useState(true);
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
        processNewEpisodesFirst={processNewEpisodesFirst}
        onProcessNewEpisodesFirstChange={setProcessNewEpisodesFirst}
        queueManualBoost={20}
        onQueueManualBoostChange={() => {}}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
      />
      <button onClick={() => onCommit({ processNewEpisodesFirst })}>Commit</button>
    </>
  );
}

describe('GlobalDefaultsSection: feed refresh interval', () => {
  it('shows the default value of 15', () => {
    render(<Harness onCommit={() => {}} />);
    expect((screen.getByLabelText('Feed refresh interval') as HTMLInputElement).value).toBe('15');
  });

  it('has min/max attributes of 5 and 1440', () => {
    render(<Harness onCommit={() => {}} />);
    const input = screen.getByLabelText('Feed refresh interval') as HTMLInputElement;
    expect(input.min).toBe('5');
    expect(input.max).toBe('1440');
  });

  it('commits rssRefreshIntervalMinutes after editing the field', async () => {
    let committed: number | null = null;
    render(<Harness onCommit={(minutes) => { committed = minutes; }} />);
    const user = userEvent.setup();

    const input = screen.getByLabelText('Feed refresh interval');
    await user.clear(input);
    await user.type(input, '30');
    input.blur();

    await user.click(screen.getByRole('button', { name: 'Commit' }));
    expect(committed).toBe(30);
  });

  it('clamps a value above the max to 1440', async () => {
    let committed: number | null = null;
    render(<Harness onCommit={(minutes) => { committed = minutes; }} />);
    const user = userEvent.setup();

    const input = screen.getByLabelText('Feed refresh interval');
    await user.clear(input);
    await user.type(input, '5000');
    input.blur();

    await user.click(screen.getByRole('button', { name: 'Commit' }));
    expect(committed).toBe(1440);
  });

  it('clamps a value below the min to 5', async () => {
    let committed: number | null = null;
    render(<Harness onCommit={(minutes) => { committed = minutes; }} />);
    const user = userEvent.setup();

    const input = screen.getByLabelText('Feed refresh interval');
    await user.clear(input);
    await user.type(input, '1');
    input.blur();

    await user.click(screen.getByRole('button', { name: 'Commit' }));
    expect(committed).toBe(5);
  });
});

describe('GlobalDefaultsSection: Podping notifications toggle', () => {
  it('renders off by default', () => {
    render(<PodpingHarness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Podping notifications' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
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
});

describe('GlobalDefaultsSection: Process new episodes first toggle', () => {
  it('renders on by default', () => {
    render(<ProcessNewFirstHarness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Process new episodes first' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });

  it('commits { processNewEpisodesFirst: false } after switching off', async () => {
    let committed: ProcessNewFirstState | null = null;
    render(<ProcessNewFirstHarness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Process new episodes first' }));
    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({ processNewEpisodesFirst: false });
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
        processNewEpisodesFirst
        onProcessNewEpisodesFirstChange={() => {}}
        queueManualBoost={20}
        onQueueManualBoostChange={() => {}}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction={lowAdYieldAction}
        onLowAdYieldActionChange={setLowAdYieldAction}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
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
        processNewEpisodesFirst
        onProcessNewEpisodesFirstChange={() => {}}
        queueManualBoost={20}
        onQueueManualBoostChange={() => {}}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={retentionDays}
        onEpisodeLogRetentionDaysChange={setRetentionDays}
        episodeLogLevel={level}
        onEpisodeLogLevelChange={setLevel}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
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
        processNewEpisodesFirst
        onProcessNewEpisodesFirstChange={() => {}}
        queueManualBoost={20}
        onQueueManualBoostChange={() => {}}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="debug"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={textRecurrenceHints}
        onTextRecurrenceHintsChange={setTextRecurrenceHints}
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

describe('GlobalDefaultsSection: queue boosts', () => {
  function renderBoosts(onManual = () => {}) {
    return render(
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
        processNewEpisodesFirst={true}
        onProcessNewEpisodesFirstChange={() => {}}
        queueManualBoost={20}
        onQueueManualBoostChange={onManual}
        queueFreshBoost={5}
        onQueueFreshBoostChange={() => {}}
        queueBulkBoost={0}
        onQueueBulkBoostChange={() => {}}
        lowAdYieldAction="nothing"
        onLowAdYieldActionChange={() => {}}
        episodeLogRetentionDays={30}
        onEpisodeLogRetentionDaysChange={() => {}}
        episodeLogLevel="info"
        onEpisodeLogLevelChange={() => {}}
        textRecurrenceHints={false}
        onTextRecurrenceHintsChange={() => {}}
      />
    );
  }

  it('renders the three boost fields with their current values', () => {
    renderBoosts();
    expect((screen.getByLabelText('Play or reprocess') as HTMLInputElement).value).toBe('20');
    expect((screen.getByLabelText('New episode') as HTMLInputElement).value).toBe('5');
    expect((screen.getByLabelText('Reprocess All') as HTMLInputElement).value).toBe('0');
  });

  it('reports an edited manual boost', () => {
    const onManual = vi.fn();
    renderBoosts(onManual);
    const input = screen.getByLabelText('Play or reprocess');
    fireEvent.change(input, { target: { value: '30' } });
    expect(onManual).toHaveBeenCalledWith(30);
  });
});
