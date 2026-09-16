import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DashboardControlsMenu from './DashboardControlsMenu';
import { DESKTOP, PHONE, rect, restoreViewport, setupPlacement } from '../test/placement';

function renderMenu(overrides: Partial<Parameters<typeof DashboardControlsMenu>[0]> = {}) {
  const view = render(
    <DashboardControlsMenu
      dashboardView="podcasts"
      viewMode="grid"
      onViewModeChange={vi.fn()}
      sortBy="recent"
      onSortChange={vi.fn()}
      episodesPerPodcast={3}
      onEpisodesPerPodcastChange={vi.fn()}
      perPodcastMin={1}
      perPodcastMax={5}
      {...overrides}
    />,
  );
  return { ...view, root: view.container.firstChild as HTMLElement };
}

const trigger = () => screen.getByRole('button', { name: 'View options' });
const panel = () => screen.queryByRole('group', { name: 'Layout and sort' });
const openPanel = () => userEvent.click(trigger());

beforeEach(() => setupPlacement(DESKTOP, { width: 256, height: 200 }));
afterEach(() => {
  vi.restoreAllMocks();
  restoreViewport();
});

describe('DashboardControlsMenu', () => {
  it('centers the panel under the trigger row on a phone', async () => {
    setupPlacement(PHONE, { width: 256, height: 200 });
    const { root } = renderMenu();
    root.getBoundingClientRect = rect({ left: 280, right: 360, top: 100, bottom: 144 });
    await openPanel();
    expect(panel()!.className).toContain('left-1/2 -translate-x-1/2');
    expect(panel()!.style.top).toBe('148px');
  });

  it('does not announce the trigger as a menu', () => {
    renderMenu();
    expect(trigger().getAttribute('aria-haspopup')).toBeNull();
  });

  it('names the layout and sort buttons with the text they show', async () => {
    renderMenu();
    await openPanel();
    expect(screen.getByRole('button', { name: 'Grid' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'List' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sort by recent' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sort by title' })).toBeTruthy();
  });

  it('swaps the layout buttons for the per-podcast count in the episodes view', async () => {
    renderMenu({ dashboardView: 'episodes' });
    await openPanel();
    expect(screen.queryByRole('button', { name: 'Grid' })).toBeNull();
    expect(screen.getByRole('combobox', { name: 'Episodes per podcast' })).toBeTruthy();
  });

  it('routes Tab from the open trigger into the first control', async () => {
    renderMenu();
    await openPanel();
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Grid' }));
  });
});
