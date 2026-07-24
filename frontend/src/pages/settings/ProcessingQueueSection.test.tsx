/**
 * Tests for the Processing Queue settings section, including the feed
 * refresh interval field added alongside the podping-listener feature.
 * Existing processing-episode list behavior is exercised indirectly via
 * the "no episodes" idle state; the interval field is the focus here.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProcessingQueueSection from './ProcessingQueueSection';

function Harness({ onCommit }: { onCommit: (minutes: number) => void }) {
  const [minutes, setMinutes] = useState(15);
  return (
    <>
      <ProcessingQueueSection
        processingEpisodes={[]}
        onCancel={() => {}}
        cancelIsPending={false}
        rssRefreshIntervalMinutes={minutes}
        onRssRefreshIntervalMinutesChange={setMinutes}
      />
      <button onClick={() => onCommit(minutes)}>Commit</button>
    </>
  );
}

describe('ProcessingQueueSection: feed refresh interval', () => {
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
