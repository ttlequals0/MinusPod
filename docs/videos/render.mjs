import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// Run from an animation folder (the one holding index.html), via its npm scripts:
//   node ../render.mjs preview [t1,t2,...] [--landscape]  |  node ../render.mjs frames [--landscape]
const landscape = process.argv.includes('--landscape');
const [, , mode, arg] = process.argv.filter((a) => a !== '--landscape');
const D = process.cwd();  // the animation folder, which also holds the playwright install
const { chromium } = createRequire(path.join(D, 'package.json'))('playwright');
const browser = await chromium.launch({ channel: 'chromium' });
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
let pageError; page.on('pageerror', (e) => { pageError = e; });
await page.goto(pathToFileURL(path.join(D, 'index.html')).href + (landscape ? '?layout=landscape' : ''));
await page.evaluate(() => window.ready);
if (pageError) throw pageError;  // a script error in index.html or lib/canvas.js
const { W, H, FPS, total, sceneMids } = await page.evaluate(() => ({ W: window.W, H: window.H, FPS: window.FPS, total: window.total, sceneMids: window.sceneMids }));
await page.setViewportSize({ width: W, height: H });
console.log('total', total.toFixed(2), 's at', FPS, 'fps,', `${W}x${H}`);
const tag = landscape ? '-landscape' : '';
const shot = async (t, file) => { await page.evaluate((t) => window.render(t), t); if (pageError) throw pageError; await page.screenshot({ path: file }); };

if (mode === 'preview') {
  fs.mkdirSync(`${D}/preview`, { recursive: true });
  for (const t of arg ? arg.split(',').map(Number) : sceneMids) await shot(t, `${D}/preview/t${t.toFixed(2).replace('.', '_')}${tag}.png`);
} else {
  fs.rmSync(`${D}/frames`, { recursive: true, force: true }); fs.mkdirSync(`${D}/frames`);
  const n = Math.round(total * FPS);
  for (let i = 0; i < n; i++) { await shot(i / FPS, `${D}/frames/${String(i).padStart(5, '0')}.png`); if (i % 300 === 0) console.log('frame', i, '/', n); }
  console.log('frames', n);
}
await browser.close();
