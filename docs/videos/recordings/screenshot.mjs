// Headless page screenshot for planning a beat: node screenshot.mjs <phone|laptop|desktop> <name> <url> [js-action...]
// Each action is the body of an async function taking the page as `p` and the targets as `T`. FULL=0 captures the viewport only.
import { chromium } from 'playwright';
import fs from 'node:fs';
import { PAGE_CSS, loadTargets, openContext, hideSensitive } from './helpers.mjs';
const [,, device, name, url, ...acts] = process.argv;
const T = loadTargets();
const browser = await chromium.launch({ channel: 'chromium' });
const ctx = await openContext(browser, T, device, { scale: 1 });
const p = await ctx.newPage();
await p.goto(T.baseUrl + url, { waitUntil: 'networkidle' });
await p.addStyleTag({ content: PAGE_CSS[device] || PAGE_CSS.other });
await p.waitForTimeout(1200);
for (const a of acts) { try { await eval(`(async (p, T) => { ${a} })`)(p, T); } catch (e) { console.log('action failed:', a, String(e).slice(0, 200)); } await p.waitForTimeout(900); }
await hideSensitive(p, T);  // so the plan shows what the take will show
fs.mkdirSync('probe', { recursive: true });
await p.screenshot({ path: `probe/${name}.png`, fullPage: process.env.FULL !== '0' });
console.log(`probe/${name}.png`);
await browser.close();
