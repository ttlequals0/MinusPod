// Manual script: captures the episode-detail docs screenshots from a live instance.
// Usage: LIVE_BASE_URL=https://host APP_PASSWORD=... node e2e/live-docs-screenshots.mjs <feedSlug> <episodeId>
import { chromium } from '@playwright/test';
import path from 'node:path';

const base = process.env.LIVE_BASE_URL?.replace(/\/$/, '');
const password = process.env.APP_PASSWORD;
const [slug, episodeId] = process.argv.slice(2);
if (!base || !password || !slug || !episodeId) {
  console.error('Set LIVE_BASE_URL and APP_PASSWORD, and pass <feedSlug> <episodeId>.');
  process.exit(1);
}
const OUT = path.resolve('..', 'docs', 'screenshots');
const sizes = [
  { name: 'desktop', width: 1280, height: 900, mobile: false },
  { name: 'mobile', width: 390, height: 844, mobile: true },
];

const browser = await chromium.launch();
for (const s of sizes) {
  const ctx = await browser.newContext({
    viewport: { width: s.width, height: s.height },
    isMobile: s.mobile, hasTouch: s.mobile, colorScheme: 'dark',
  });
  await ctx.addInitScript(() => localStorage.setItem('theme', 'dark'));
  const page = await ctx.newPage();
  await page.goto(`${base}/ui/login`);
  await page.fill('#password', password);
  await page.keyboard.press('Enter');
  await page.waitForURL((u) => !u.pathname.endsWith('/login'));
  await page.goto(`${base}/ui/feeds/${slug}/episodes/${episodeId}`);
  const player = page.getByRole('group', { name: 'Episode player' });
  await player.waitFor();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: 'Pause', exact: true }).waitFor();
  const elapsed = player.locator('div.tabular-nums > span').first();
  const before = await elapsed.textContent();
  await page.getByRole('button', { name: 'Next chapter' }).click();
  await page.waitForFunction(
    ([sel, b]) => document.querySelector(sel)?.textContent !== b,
    ['[aria-label="Episode player"] div.tabular-nums > span', before],
  );
  await page.waitForTimeout(1500);
  await page.mouse.move(0, 0);
  console.log(s.name, 'chapter:', await player.locator('p[title]').first().textContent(), 'elapsed:', await elapsed.textContent());
  await page.screenshot({ path: path.join(OUT, `episode-detail-${s.name}.png`) });
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await ctx.close();
}
await browser.close();
