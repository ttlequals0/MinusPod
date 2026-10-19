import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

// Usage: node render.mjs preview <t1,t2,...>  |  node render.mjs frames
// Needs playwright with Chromium on the module path, for example: npx -y -p playwright node render.mjs frames
const [, , mode, arg] = process.argv;
const D = path.dirname(new URL(import.meta.url).pathname);
const FPS = 30;
const browser = await chromium.launch({ channel: 'chromium' });
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => { console.error('PAGE ERROR', e.message); process.exitCode = 1; });
await page.goto('file://' + D + '/index.html');
await page.evaluate(() => window.ready); await page.evaluate(() => document.fonts.ready);
const total = await page.evaluate(() => window.total);
console.log('total', total.toFixed(2), 's');
const shot = async (t, file) => { await page.evaluate((t) => window.render(t), t); await page.screenshot({ path: file, clip: { x: 0, y: 0, width: 1080, height: 1920 } }); };

if (mode === 'preview') {
  fs.mkdirSync(`${D}/preview`, { recursive: true });
  for (const t of arg.split(',').map(Number)) await shot(t, `${D}/preview/t${t.toFixed(2).replace('.', '_')}.png`);
} else {
  fs.rmSync(`${D}/frames`, { recursive: true, force: true }); fs.mkdirSync(`${D}/frames`);
  const n = Math.round(total * FPS);
  for (let i = 0; i < n; i++) { await shot(i / FPS, `${D}/frames/${String(i).padStart(5, '0')}.png`); if (i % 300 === 0) console.log('frame', i, '/', n); }
  console.log('frames', n);
}
await browser.close();
