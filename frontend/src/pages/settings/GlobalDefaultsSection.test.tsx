/**
 * Tests for the Global Defaults settings section, including the feed
 * refresh interval field and the Podping notifications toggle added
 * alongside the podping-listener feature.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GlobalDefaultsSection from './GlobalDefaultsSection';

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
      />
      <button onClick={() => onCommit({ podpingEnabled })}>Commit</button>
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
