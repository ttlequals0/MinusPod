/**
 * Tests for the Chapter density group in the Transcripts and chapters section.
 */
import { beforeEach, describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Podcasting20Section from './Podcasting20Section';
import type { UpdateSettingsPayload } from '../../api/types';
import { baseDefaults, baseTunables, inputFor } from './tunablesTestFixtures';
import { expectGatedByLlm, expectNotGatedByLlm } from '../../test/llmGate';
import { LLM_DISABLED_REASON } from './LlmGate';
import { llmState, setLlmEnabled } from '../../test/llmEnabled';

vi.mock('../../hooks/useLlmEnabled', async () => (await import('../../test/llmEnabled')).llmEnabledMock);
afterEach(() => { llmState.enabled = true; });

function Harness({
  llmEnabled = true,
  chaptersMode = 'auto',
  onSave = () => {},
  onChaptersInNotesChange = () => {},
  onReviewLinksInNotesChange = () => {},
}: {
  llmEnabled?: boolean;
  chaptersMode?: 'auto' | 'generate' | 'off';
  onSave?: (payload: UpdateSettingsPayload) => void;
  onChaptersInNotesChange?: (enabled: boolean) => void;
  onReviewLinksInNotesChange?: (enabled: boolean) => void;
}) {
  setLlmEnabled(llmEnabled);
  return (
    <Podcasting20Section
      vttTranscriptsEnabled
      chaptersEnabled
      chaptersInNotes={false}
      reviewLinksInNotes={false}
      onVttTranscriptsEnabledChange={() => {}}
      onChaptersEnabledChange={() => {}}
      onChaptersInNotesChange={onChaptersInNotesChange}
      onReviewLinksInNotesChange={onReviewLinksInNotesChange}
      chaptersMode={chaptersMode}
      onChaptersModeChange={() => {}}
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

beforeEach(() => {
  localStorage.setItem('settings-section-transcripts-&-chapters', 'true');
});

describe('chapter density controls', () => {
  const FIELDS: Array<[string, string, number]> = [
    ['Target chapter length (seconds)', 'chapterTargetSeconds', 900],
    ['Transcript window (seconds)', 'chapterWindowSeconds', 3600],
    ['Maximum chapters', 'chapterMaxBoundaries', 25],
    ['Shortest chapter (seconds)', 'chapterMinDurationSeconds', 120],
  ];

  it('renders all four density fields under their own heading', () => {
    render(<Harness />);
    expect(screen.getByText('Chapter density')).toBeTruthy();
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
    await user.click(screen.getByRole('button', { name: 'Save chapter density' }));

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
    expect(screen.getByRole('button', { name: 'Save chapter density' })
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
    expect(screen.getByRole('button', { name: 'Save chapter density' })
      .hasAttribute('disabled')).toBe(true);
  });
});

describe.each([
  { label: 'chapters', name: 'List chapters in episode descriptions', prop: 'onChaptersInNotesChange' },
  { label: 'review links', name: 'Add review links to episode descriptions', prop: 'onReviewLinksInNotesChange' },
])('$label in episode description toggle', ({ name, prop }) => {
  it('fires the change handler when switched on', async () => {
    const onChange = vi.fn();
    render(<Harness {...{ [prop]: onChange }} />);
    await userEvent.click(screen.getByRole('switch', { name }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe('Transcripts and chapters: LLM provider None', () => {
  it('disables Always generate and keeps Auto and Off, with the reason beside the mode select', () => {
    render(<Harness llmEnabled={false} chaptersMode="generate" />);
    const mode = screen.getByLabelText('Chapter mode for feeds that inherit the global setting') as HTMLSelectElement;
    expect((screen.getByRole('option', { name: 'Always generate' }) as HTMLOptionElement).disabled).toBe(true);
    expect((screen.getByRole('option', { name: 'Auto' }) as HTMLOptionElement).disabled).toBe(false);
    expect((screen.getByRole('option', { name: 'Off' }) as HTMLOptionElement).disabled).toBe(false);
    expect(document.getElementById(mode.getAttribute('aria-describedby')!)?.textContent).toBe(
      `${LLM_DISABLED_REASON} Always generate behaves like Auto.`,
    );
  });

  it('keeps the plain reason when the saved mode is not generate', () => {
    render(<Harness llmEnabled={false} chaptersMode="auto" />);
    const mode = screen.getByLabelText('Chapter mode for feeds that inherit the global setting');
    expect(document.getElementById(mode.getAttribute('aria-describedby')!)?.textContent).toBe(LLM_DISABLED_REASON);
  });

  it('disables the chapter density group, whose calls need a model', () => {
    render(<Harness llmEnabled={false} />);
    expectGatedByLlm(screen.getByRole('button', { name: 'Save chapter density' }));
  });

  it('keeps the other output toggles enabled', () => {
    render(<Harness llmEnabled={false} />);
    expectNotGatedByLlm(screen.getByRole('switch', { name: 'Generate chapters' }));
  });

  it('offers Always generate when the provider is set', () => {
    render(<Harness />);
    expect((screen.getByRole('option', { name: 'Always generate' }) as HTMLOptionElement).disabled).toBe(false);
    expectNotGatedByLlm(screen.getByRole('button', { name: 'Save chapter density' }));
  });
});
