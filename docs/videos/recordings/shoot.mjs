// Records one clip per beat from a running MinusPod instance, on a virtual X display, with ffmpeg capturing the screen.
// Usage: MINUSPOD_SESSION=<session cookie> node shoot.mjs beats/<video>.mjs <phone|laptop|desktop> <beat...> [--display :97]
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { DEVICES, PAGE_CSS, loadTargets, openContext, sleep, hideSensitive, leakCheck, guard } from './helpers.mjs';

const args = process.argv.slice(2);
const di = args.indexOf('--display'); const display = di >= 0 ? args.splice(di, 2)[1] : null;
const [beatsFile, device, ...beatNames] = args;
const DEV = DEVICES[device]; if (!DEV || !beatsFile || !beatNames.length) { console.error('usage: node shoot.mjs beats/<video>.mjs <phone|laptop|desktop> <beat...> [--display :N]'); process.exit(2); }
const DISPLAY = display || DEV.display;
const T = loadTargets();

const { BEATS } = await import(pathToFileURL(path.resolve(beatsFile)).href);
const outDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'clips', path.basename(beatsFile, '.mjs'));
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ channel: 'chromium', headless: false, env: { ...process.env, DISPLAY },
  args: [`--force-device-scale-factor=${DEV.DSF}`, '--window-position=0,0', '--hide-scrollbars', '--disable-infobars', '--no-first-run', '--autoplay-policy=no-user-gesture-required'] });
const ctx = await openContext(browser, T, device);
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);
const { windowId } = await cdp.send('Browser.getWindowForTarget');
await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'fullscreen' } });
await page.goto(T.baseUrl + '/', { waitUntil: 'networkidle' });
await sleep(6500); // Chromium's fullscreen hint bubble fades out
const inner = await page.evaluate(() => [window.innerWidth, window.innerHeight]);
if (inner[0] !== DEV.W / DEV.DSF || inner[1] !== DEV.H / DEV.DSF) throw new Error(`viewport ${inner}; is the Xvfb screen ${DEV.W}x${DEV.H}?`);

for (const name of beatNames) {
  const beat = BEATS[name]; if (!beat || beat.dev !== device) throw new Error(`no ${device} beat named ${name}`);
  await page.unrouteAll({ behavior: 'ignoreErrors' }); if (beat.mock) await beat.mock(page, T);  // in-page mocks, registered before navigation
  await page.goto(T.baseUrl + (typeof beat.url === 'function' ? beat.url(T) : beat.url), { waitUntil: 'networkidle' });
  await page.addStyleTag({ content: PAGE_CSS[device] || PAGE_CSS.other });
  await sleep(600);
  if (beat.prep) await beat.prep(page, T);  // off-camera setup
  await page.mouse.move(2, 2); await hideSensitive(page, T); await leakCheck(page, T, name);
  const out = `${outDir}/${name}.mp4`;
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '30', '-video_size', `${DEV.W}x${DEV.H}`, '-i', `${DISPLAY}.0+0,0`, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '12', '-pix_fmt', 'yuv420p', out]);
  await sleep(700);
  const t0 = Date.now(), stopGuard = guard(page, T, name);
  try { await beat.run(page, T); await stopGuard(); await hideSensitive(page, T); await leakCheck(page, T, name + ' (end)'); }
  finally { await stopGuard().catch(() => {}); ff.kill('SIGINT'); await new Promise((r) => ff.on('close', r)); }
  console.log('recorded', out, ((Date.now() - t0) / 1000).toFixed(1), 's');
}
await browser.close();
