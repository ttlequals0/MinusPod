import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Usage: node render.mjs preview [t1,t2,...]  |  npm run frames
const [, , mode, arg] = process.argv;
const D = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({ channel: 'chromium' });
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
let pageError; page.on('pageerror', (e) => { pageError = e; });
await page.goto(pathToFileURL(path.join(D, 'index.html')).href);
await page.evaluate(() => window.ready);
const { W, H, FPS, total, sceneMids } = await page.evaluate(() => ({ W: window.W, H: window.H, FPS: window.FPS, total: window.total, sceneMids: window.sceneMids }));
await page.setViewportSize({ width: W, height: H });
console.log('total', total.toFixed(2), 's at', FPS, 'fps');
const shot = async (t, file) => { await page.evaluate((t) => window.render(t), t); if (pageError) throw pageError; await page.screenshot({ path: file }); };

if (mode === 'preview') {
  fs.mkdirSync(`${D}/preview`, { recursive: true });
  for (const t of arg ? arg.split(',').map(Number) : sceneMids) await shot(t, `${D}/preview/t${t.toFixed(2).replace('.', '_')}.png`);
} else {
  fs.rmSync(`${D}/frames`, { recursive: true, force: true }); fs.mkdirSync(`${D}/frames`);
  const n = Math.round(total * FPS);
  for (let i = 0; i < n; i++) { await shot(i / FPS, `${D}/frames/${String(i).padStart(5, '0')}.png`); if (i % 300 === 0) console.log('frame', i, '/', n); }
  console.log('frames', n);
}
await browser.close();
