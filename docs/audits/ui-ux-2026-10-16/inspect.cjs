const { chromium } = require('../../../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const out = __dirname;
const routes = ['add', 'search', 'patterns', 'history', 'history?tab=downloads', 'sponsors', 'queue', 'settings', '', 'stats', 'login'];
const widths = [320, 360, 390, 640, 768, 1024, 1280, 1440];

if (process.argv.includes('--validate')) {
  require('./validate.cjs');
  return;
}
if (process.argv.includes('--previews')) {
  require('./previews.cjs');
  return;
}
if (process.argv.includes('--checks')) {
  require('./checks.cjs');
  return;
}
if (process.argv.includes('--settings')) {
  require('./settings-proof.cjs');
  return;
}

async function measure(page) {
  return page.evaluate(() => {
    const visible = (e) => e.getBoundingClientRect().width && e.getBoundingClientRect().height && getComputedStyle(e).visibility !== 'hidden';
    const box = (e) => {
      const r = e.getBoundingClientRect();
      return { label: (e.getAttribute('aria-label') || e.innerText || e.getAttribute('placeholder') || e.tagName).trim().slice(0, 100), x: r.x, y: r.y, width: r.width, height: r.height, font: getComputedStyle(e).fontSize };
    };
    const controls = [...document.querySelectorAll('button,input,select,textarea,summary,[role="switch"]')].filter(visible).map(box);
    return {
      width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      text: document.body.innerText,
      smallControls: controls.filter(r => r.width < 44 || r.height < 44),
      overflow: [...document.querySelectorAll('main *')].filter(visible).filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(box).slice(0, 20),
      dialogs: [...document.querySelectorAll('[role="dialog"]')].map(e => ({ ...box(e), label: e.getAttribute('aria-label'), labelledBy: e.getAttribute('aria-labelledby') })),
    };
  });
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, colorScheme: 'light', hasTouch: true });
  await context.addInitScript(() => { localStorage.setItem('theme', 'light'); localStorage.setItem('themeId', 'slate'); });
  fs.mkdirSync(path.join(out, 'images'), { recursive: true });
  const results = [];
  for (const route of routes) {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:5173/ui/${route}`);
    await page.waitForTimeout(11500);
    const name = route.replace('?tab=', '-') || 'dashboard';
    const item = { route, errors, measurements: [] };
    for (const width of widths) {
      await page.setViewportSize({ width, height: width < 640 ? 844 : 1000 });
      await page.waitForTimeout(100);
      item.measurements.push(await measure(page));
      if (width === 390 || width === 1440) await page.screenshot({ path: path.join(out, 'images', `${name}-${width}-before.png`), fullPage: false });
    }
    results.push(item);
    fs.writeFileSync(path.join(out, 'runtime-evidence.json'), JSON.stringify(results, null, 2));
    console.log(`${name}: ${errors.length} page errors; overflow at ${item.measurements.filter(x => x.scrollWidth > x.width).map(x => x.width).join(', ') || 'none'} px`);
    await page.close();
  }
  await browser.close();
})();
