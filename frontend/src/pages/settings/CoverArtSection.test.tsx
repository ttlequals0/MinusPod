/**
 * Tests for the badge position select in the Cover Art section (issue #600).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CoverArtSection from './CoverArtSection';
import type { BadgePosition } from '../../api/types';

function Harness({
  watermarkEnabled = true,
  onPositionChange = () => {},
}: {
  watermarkEnabled?: boolean;
  onPositionChange?: (position: BadgePosition) => void;
}) {
  return (
    <CoverArtSection
      artworkWatermarkEnabled={watermarkEnabled}
      onArtworkWatermarkEnabledChange={() => {}}
      artworkBadgePosition="bottom-right"
      onArtworkBadgePositionChange={onPositionChange}
      maxArtworkBytes={26214400}
      onMaxArtworkBytesChange={() => {}}
      onRefreshArtwork={() => {}}
      refreshArtworkPending={false}
    />
  );
}

describe('badge position select', () => {
  it('renders all four corners with bottom-right selected', () => {
    render(<Harness />);
    const select = screen.getByLabelText('Badge position') as HTMLSelectElement;
    expect(select.value).toBe('bottom-right');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'Bottom right', 'Bottom left', 'Top right', 'Top left',
    ]);
  });

  it('reports the chosen corner', async () => {
    let saved: BadgePosition | null = null;
    render(<Harness onPositionChange={(position) => { saved = position; }} />);
    await userEvent.selectOptions(screen.getByLabelText('Badge position'), 'top-left');
    expect(saved).toBe('top-left');
  });

  it('is disabled while the badge toggle is off', () => {
    render(<Harness watermarkEnabled={false} />);
    expect((screen.getByLabelText('Badge position') as HTMLSelectElement).disabled).toBe(true);
  });
});
