/**
 * Tests for the Transcripts & Chapters settings section, including the
 * Podping notifications toggle added alongside the podping-listener feature.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Podcasting20Section from './Podcasting20Section';

interface ToggleState {
  vttTranscriptsEnabled: boolean;
  chaptersEnabled: boolean;
  podpingEnabled: boolean;
}

function defaultState(): ToggleState {
  return {
    vttTranscriptsEnabled: false,
    chaptersEnabled: false,
    podpingEnabled: false,
  };
}

// Mirrors how Settings.tsx wires this section: controlled state lifted to
// the parent, with a single Commit action standing in for the page's
// batched "Save Changes" button.
function Harness({ onCommit }: { onCommit: (payload: ToggleState) => void }) {
  const [state, setState] = useState<ToggleState>(defaultState());
  const patch = <K extends keyof ToggleState>(key: K) => (v: ToggleState[K]) =>
    setState((s) => ({ ...s, [key]: v }));
  return (
    <>
      <Podcasting20Section
        vttTranscriptsEnabled={state.vttTranscriptsEnabled}
        chaptersEnabled={state.chaptersEnabled}
        podpingEnabled={state.podpingEnabled}
        onVttTranscriptsEnabledChange={patch('vttTranscriptsEnabled')}
        onChaptersEnabledChange={patch('chaptersEnabled')}
        onPodpingEnabledChange={patch('podpingEnabled')}
      />
      <button onClick={() => onCommit(state)}>Commit</button>
    </>
  );
}

describe('Podcasting20Section: Podping notifications toggle', () => {
  it('renders off by default', () => {
    render(<Harness onCommit={() => {}} />);
    const toggle = screen.getByRole('switch', { name: 'Podping notifications' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('commits { podpingEnabled: true } after switching on', async () => {
    let committed: ToggleState | null = null;
    render(<Harness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Podping notifications' }));
    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({
      vttTranscriptsEnabled: false,
      chaptersEnabled: false,
      podpingEnabled: true,
    });
  });

  it('commits { podpingEnabled: false } after switching on then off again', async () => {
    let committed: ToggleState | null = null;
    render(<Harness onCommit={(payload) => { committed = payload; }} />);
    const user = userEvent.setup();

    const toggle = screen.getByRole('switch', { name: 'Podping notifications' });
    await user.click(toggle);
    await user.click(toggle);
    await user.click(screen.getByRole('button', { name: 'Commit' }));

    expect(committed).toEqual({
      vttTranscriptsEnabled: false,
      chaptersEnabled: false,
      podpingEnabled: false,
    });
  });
});
