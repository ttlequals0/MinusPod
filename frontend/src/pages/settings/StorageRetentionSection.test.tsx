import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import StorageRetentionSection from './StorageRetentionSection';

function renderSection(overrides: Partial<Parameters<typeof StorageRetentionSection>[0]> = {}) {
  return render(
    <StorageRetentionSection
      keepOriginalAudio
      onKeepOriginalAudioChange={() => {}}
      keepOriginalSaveIsPending={false}
      retentionEnabled
      retentionDays={30}
      onRetentionEnabledChange={() => {}}
      onRetentionDaysChange={() => {}}
      originalRetentionDays={30}
      onOriginalRetentionDaysChange={() => {}}
      unfetchedRetentionDays={0}
      onUnfetchedRetentionDaysChange={() => {}}
      fetchedRetentionGraceDays={3}
      onFetchedRetentionGraceDaysChange={() => {}}
      onSave={() => {}}
      saveIsPending={false}
      saveIsSuccess={false}
      {...overrides}
    />,
  );
}

beforeEach(() => {
  localStorage.setItem('settings-section-storage-retention', 'true');
});

describe('StorageRetentionSection: unfetched retention', () => {
  it('disables the grace field while unfetched retention is off', () => {
    renderSection();
    expect((screen.getByLabelText('Keep never-downloaded episodes for:') as HTMLInputElement).value).toBe('0');
    expect((screen.getByLabelText('Keep after first download for:') as HTMLInputElement).disabled).toBe(true);
  });

  it('enables the grace field once unfetched retention is set', () => {
    renderSection({ unfetchedRetentionDays: 90 });
    const grace = screen.getByLabelText('Keep after first download for:') as HTMLInputElement;
    expect(grace.disabled).toBe(false);
    expect(grace.value).toBe('3');
  });

  it('disables both fields when retention is off', () => {
    renderSection({ retentionEnabled: false, unfetchedRetentionDays: 90 });
    expect((screen.getByLabelText('Keep never-downloaded episodes for:') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Keep after first download for:') as HTMLInputElement).disabled).toBe(true);
  });
});
