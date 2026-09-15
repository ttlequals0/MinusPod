import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DashboardControlsMenu from './DashboardControlsMenu';

const PHONE_WIDTH_PX = 375;
const defaultWidth = window.innerWidth;
const setViewportWidth = (px: number) =>
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: px });

afterEach(() => setViewportWidth(defaultWidth));

function renderMenu(overrides: Partial<Parameters<typeof DashboardControlsMenu>[0]> = {}) {
  return render(
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
}

async function openPanel() {
  await userEvent.click(screen.getByRole('button', { name: 'View options' }));
  return screen.getByRole('group', { name: 'Layout and sort' });
}

describe('DashboardControlsMenu: panel stays with its trigger', () => {
  it('closes a phone panel when the page scrolls', async () => {
    setViewportWidth(PHONE_WIDTH_PX);
    renderMenu();
    await openPanel();
    fireEvent.scroll(window);
    expect(screen.queryByRole('group', { name: 'Layout and sort' })).toBeNull();
  });

  it('closes a phone panel when the viewport resizes', async () => {
    setViewportWidth(PHONE_WIDTH_PX);
    renderMenu();
    await openPanel();
    fireEvent(window, new Event('resize'));
    expect(screen.queryByRole('group', { name: 'Layout and sort' })).toBeNull();
  });

  it('keeps an anchored panel open when a scroll happens at desktop width', async () => {
    renderMenu();
    await openPanel();
    fireEvent.scroll(window);
    expect(screen.queryByRole('group', { name: 'Layout and sort' })).toBeTruthy();
  });
});

describe('DashboardControlsMenu: accessible names', () => {
  it('names the layout buttons with the text they show', async () => {
    renderMenu();
    await openPanel();
    expect(screen.getByRole('button', { name: 'Grid' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'List' })).toBeTruthy();
  });

  it('does not announce the trigger as a menu', () => {
    renderMenu();
    const trigger = screen.getByRole('button', { name: 'View options' });
    expect(trigger.getAttribute('aria-haspopup')).toBeNull();
  });
});
