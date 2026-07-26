/**
 * Tests for the Global Defaults settings section, including the feed
 * refresh interval field and the Podping notifications toggle added
 * alongside the podping-listener feature.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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
        segmentCategoryActions={{}}
        onSegmentCategoryActionChange={() => {}}
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
        segmentCategoryActions={{}}
        onSegmentCategoryActionChange={() => {}}
      />
      <button onClick={() => onCommit({ podpingEnabled })}>Commit</button>
    </>
  );
}

// Mirrors the "matrix PUT payload" saved immediately per row: the harness
// builds the same partial-map shape Settings.tsx sends through
// updateSettings({ segmentCategoryActions: { [category]: action } }).
function SegmentActionsHarness({ onCommit }: {
  onCommit: (payload: { segmentCategoryActions: Record<string, string> }) => void;
}) {
  const [actions, setActions] = useState<Record<string, string>>({});
  return (
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
      segmentCategoryActions={actions}
      onSegmentCategoryActionChange={(category, action) => {
        setActions((prev) => ({ ...prev, [category]: action }));
        onCommit({ segmentCategoryActions: { [category]: action } });
      }}
    />
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

describe('GlobalDefaultsSection: Segment actions matrix', () => {
  it('collapses the matrix by default and expands on click', async () => {
    const { container } = render(<SegmentActionsHarness onCommit={() => {}} />);
    const details = container.querySelector('details');
    expect(details).toBeTruthy();
    expect(details!.open).toBe(false);
    await userEvent.click(screen.getByText('Segment actions'));
    expect(details!.open).toBe(true);
  });

  it('renders all seven category rows with Remove selected by default', async () => {
    render(<SegmentActionsHarness onCommit={() => {}} />);
    await userEvent.click(screen.getByText('Segment actions'));
    for (const label of ['Sponsor', 'Cross-promo', 'Self-promo', 'Interaction', 'Intro', 'Outro', 'Recap']) {
      const group = screen.getByRole('radiogroup', { name: `${label} action` });
      const removeBtn = within(group).getByRole('radio', { name: 'Remove' });
      expect(removeBtn.getAttribute('aria-checked')).toBe('true');
    }
  });

  it('clicking Keep on Cross-promo commits { segmentCategoryActions: { cross_promo: "keep" } }', async () => {
    let committed: { segmentCategoryActions: Record<string, string> } | null = null;
    render(<SegmentActionsHarness onCommit={(payload) => { committed = payload; }} />);
    await userEvent.click(screen.getByText('Segment actions'));

    const group = screen.getByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(within(group).getByRole('radio', { name: 'Keep' }));

    expect(committed).toEqual({ segmentCategoryActions: { cross_promo: 'keep' } });
  });

  it('clicking Beep on Intro commits { segmentCategoryActions: { intro: "beep" } } and leaves other rows untouched', async () => {
    const commits: Array<{ segmentCategoryActions: Record<string, string> }> = [];
    render(<SegmentActionsHarness onCommit={(payload) => commits.push(payload)} />);
    await userEvent.click(screen.getByText('Segment actions'));

    const introGroup = screen.getByRole('radiogroup', { name: 'Intro action' });
    await userEvent.click(within(introGroup).getByRole('radio', { name: 'Beep' }));

    expect(commits).toEqual([{ segmentCategoryActions: { intro: 'beep' } }]);
    const sponsorGroup = screen.getByRole('radiogroup', { name: 'Sponsor action' });
    expect(within(sponsorGroup).getByRole('radio', { name: 'Remove' }).getAttribute('aria-checked')).toBe('true');
  });
});
