import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';
import type { Feed } from '../src/api/types';

const base = process.env.MINUSPOD_POPULATED_URL;
test.use({ baseURL: base ? `${base.replace(/\/$/, '')}/` : undefined, serviceWorkers: 'block' });
test.skip(!base, 'Requires a populated, disposable audit instance.');

test.beforeEach(async ({ page }) => {
  // A database backup has no audio; missing media must not enqueue processing.
  await page.route(/\/episodes\/.*\.(mp3|m4a|wav|ogg)(?:\?|$)/, route => route.abort('blockedbyclient'));
});

test('real episode player fits every speed in both modes and reports unavailable audio', async ({ page, request }) => {
  const feeds = (await (await request.get(new URL('/api/v1/feeds', base!).href)).json()).feeds as Feed[];
  const feed = feeds.find(feed => feed.feedType === 'youtube')!;
  expect(feed).toBeDefined();
  const response = await request.get(new URL(`/api/v1/feeds/${feed.slug}/episodes`, base!).href);
  const episodes = (await response.json()).episodes as { episodeId: string; status: string }[];
  const episode = episodes.find(episode => episode.status === 'completed')!;
  expect(episode).toBeDefined();
  for (const theme of ['light', 'dark']) {
    await page.addInitScript(theme => localStorage.setItem('theme', theme), theme);
    await page.goto(`feeds/${feed.slug}/episodes/${episode.episodeId}`);
    const player = page.getByRole('group', { name: 'Episode player' });
    await expect(player).toBeVisible();
    await expect(player.getByRole('alert')).toContainText('Could not play this audio.');
    await expect(player.getByRole('button', { name: 'Retry playback' })).toBeVisible();
    await expect(player.getByRole('slider', { name: 'Seek' })).toBeDisabled();
    for (let speed = 0; speed < 6; speed++) {
      const box = (await player.boundingBox())!;
      const play = (await player.getByRole('button', { name: 'Play', exact: true }).boundingBox())!;
      expect(Math.abs((play.x + play.width / 2) - (box.x + box.width / 2))).toBeLessThanOrEqual(1);
      for (const button of await player.getByRole('button').all()) {
        const target = (await button.boundingBox())!;
        expect(target.x).toBeGreaterThanOrEqual(box.x);
        expect(target.x + target.width).toBeLessThanOrEqual(box.x + box.width);
        if (test.info().project.use.hasTouch) {
          expect(target.height).toBeGreaterThanOrEqual(44);
          expect(target.width).toBeGreaterThanOrEqual(44);
        }
      }
      await player.getByRole('button', { name: /Playback speed/ }).click();
    }
    const scan = await new AxeBuilder({ page }).include('[aria-label="Episode player"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(scan.violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([]);
    await test.info().attach(`player-${theme}`, { body: await player.screenshot(), contentType: 'image/png' });
    const more = page.getByRole('button', { name: 'Show more', exact: true });
    if (await more.isVisible()) {
      const bodyId = await more.getAttribute('aria-controls');
      const links = page.locator(`[id="${bodyId}"]`).getByRole('link');
      if (await links.count()) {
        await links.last().focus();
        await expect(page.getByRole('button', { name: 'Show less', exact: true })).toHaveAttribute('aria-expanded', 'true');
        await expect(links.last()).toBeInViewport();
      }
    }
  }
});

test('YouTube settings retry a failed request and protect independent drafts', async ({ page }) => {
  await page.route('**/api/v1/settings/youtube', route => route.abort('failed'));
  await page.goto('settings');
  const section = page.locator('[data-search-key="settings-section-youtube"]');
  await section.getByRole('button', { name: 'YouTube', exact: true }).click();
  await expect(section.getByRole('alert')).toContainText('Could not load YouTube settings.', { timeout: 15_000 });
  await page.unroute('**/api/v1/settings/youtube');
  await section.getByRole('button', { name: 'Retry', exact: true }).click();
  const key = section.getByLabel('YouTube Data API key (optional)');
  await expect(key).toBeVisible();
  const scan = await new AxeBuilder({ page }).include('[data-search-key="settings-section-youtube"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(scan.violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([]);
  // Use an existing visible value for a draft; never submit credentials.
  const proxy = section.getByLabel('Proxy URL');
  const original = await proxy.inputValue();
  await proxy.fill(original + ' ');
  await key.fill(await section.getByRole('heading', { name: 'YouTube', exact: true }).innerText());
  await page.getByRole('link', { name: 'MinusPod', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(key).toHaveValue('YouTube');
  await key.clear();
  await proxy.fill(original);
});

test('YouTube feed metadata and reprocessing dialogs retain context on short screens', async ({ page, request }) => {
  const feeds = (await (await request.get(new URL('/api/v1/feeds', base!).href)).json()).feeds as Feed[];
  const feed = feeds.find(feed => feed.feedType === 'youtube')!;
  await page.goto(`feeds/${feed.slug}`);
  const section = page.locator(`[data-search-key="youtube-feed-${feed.slug}"]`);
  await section.getByRole('button', { name: /^YouTube/ }).click();
  const author = section.getByLabel('Author', { exact: true });
  await expect(author).toBeVisible();
  const original = await author.inputValue();
  await author.fill(feed.title);
  await expect(section.getByRole('button', { name: 'Save metadata' })).toBeEnabled();
  await section.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(author).toHaveValue(original);
  await expect(page.getByRole('heading', { name: 'Feed settings', exact: true })).toHaveCount(1);
  await section.getByRole('combobox', { name: 'Delay mode' }).selectOption('custom');
  const delay = section.getByRole('spinbutton', { name: 'Delay', exact: true });
  await delay.fill('-1');
  await delay.blur();
  await expect(delay).toHaveAttribute('aria-invalid', 'true');
  await section.getByRole('combobox', { name: 'Delay mode' }).selectOption('global');
  const scan = await new AxeBuilder({ page }).include(`[data-search-key="youtube-feed-${feed.slug}"]`).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(scan.violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([]);
  await page.setViewportSize({ width: page.viewportSize()!.width, height: 480 });
  const trigger = page.getByTitle('Reprocess all processed episodes', { exact: true });
  await trigger.click();
  await page.getByRole('menuitem', { name: /Full reprocess/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('SponsorBlock marks and captions are refreshed');
  const cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
  await expect(cancel).toBeInViewport();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});
