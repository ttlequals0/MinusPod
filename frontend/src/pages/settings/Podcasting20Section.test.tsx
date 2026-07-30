/**
 * Tests for the Chapter Density group in the Transcripts & Chapters section.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Podcasting20Section from './Podcasting20Section';
import type { UpdateSettingsPayload } from '../../api/types';
import { baseDefaults, baseTunables, inputFor } from './tunablesTestFixtures';

function Harness({
  onSave = () => {},
}: {
  onSave?: (payload: UpdateSettingsPayload) => void;
}) {
  return (
    <Podcasting20Section
      vttTranscriptsEnabled
      chaptersEnabled
      onVttTranscriptsEnabledChange={() => {}}
      onChaptersEnabledChange={() => {}}
      geometry={{
        tunables: baseTunables,
        defaults: baseDefaults,
        onSave,
        saveIsPending: false,
        saveIsSuccess: false,
        saveError: null,
      }}
    />
  );
}

describe('chapter density controls', () => {
  const FIELDS: Array<[string, string, number]> = [
    ['Target chapter length (seconds)', 'chapterTargetSeconds', 900],
    ['Transcript window (seconds)', 'chapterWindowSeconds', 3600],
    ['Maximum chapters', 'chapterMaxBoundaries', 25],
    ['Shortest chapter (seconds)', 'chapterMinDurationSeconds', 120],
  ];

  it('renders all four density fields under their own heading', () => {
    render(<Harness />);
    expect(screen.getByText('Chapter Density')).toBeTruthy();
    for (const [label] of FIELDS) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  // Asserting the payload proves each rendered field reaches the save payload
  // without exporting internals.
  it.each(FIELDS)('saves %s', async (label, payloadKey, value) => {
    let saved: UpdateSettingsPayload | null = null;
    render(<Harness onSave={(payload) => { saved = payload; }} />);
    const user = userEvent.setup();

    await user.clear(inputFor(label));
    await user.type(inputFor(label), String(value));
    await user.click(screen.getByRole('button', { name: 'Save Chapter Density' }));

    expect(saved).toEqual({ [payloadKey]: value });
  });

  it('blocks a save when the target exceeds the transcript window', async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.clear(inputFor('Transcript window (seconds)'));
    await user.type(inputFor('Transcript window (seconds)'), '600');
    await user.clear(inputFor('Target chapter length (seconds)'));
    await user.type(inputFor('Target chapter length (seconds)'), '3600');
    expect(screen.getByText(
      'Target chapter length must not exceed the transcript window.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save Chapter Density' })
      .hasAttribute('disabled')).toBe(true);
  });

  it('blocks a save when the shortest chapter exceeds the target', async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.clear(inputFor('Target chapter length (seconds)'));
    await user.type(inputFor('Target chapter length (seconds)'), '120');
    await user.clear(inputFor('Shortest chapter (seconds)'));
    await user.type(inputFor('Shortest chapter (seconds)'), '900');
    expect(screen.getByText(
      'Shortest chapter must not exceed the target chapter length.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save Chapter Density' })
      .hasAttribute('disabled')).toBe(true);
  });
});
