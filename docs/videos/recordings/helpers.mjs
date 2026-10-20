// Shared page helpers for the recorder, the screenshot tool and the beat modules.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const D = path.dirname(fileURLToPath(import.meta.url));
// Device pixel sizes; each matches an Xvfb screen described in docs/videos/README.md.
export const DEVICES = JSON.parse(fs.readFileSync(path.join(D, 'devices.json'), 'utf8'));
// targets.json names the instance, the feeds and episodes each video uses, and the strings that must never be on screen.
export function loadTargets() {
  const file = process.env.TARGETS || path.join(D, 'targets.json');
  if (!fs.existsSync(file)) throw new Error(`missing ${file}: copy targets.example.json and fill it in`);
  const T = JSON.parse(fs.readFileSync(file, 'utf8'));
  T.sensitive = [...T.leakPatterns, ...(T.excludeShows || [])].join('|');
  return T;
}
// The web UI session cookie, from MINUSPOD_SESSION or a cookies.txt file named by MINUSPOD_COOKIES.
export function sessionCookie() {
  const fromFile = process.env.MINUSPOD_COOKIES && fs.readFileSync(process.env.MINUSPOD_COOKIES, 'utf8').split('\n').map((l) => l.split('\t')).find((f) => f[5] === 'session')?.[6];
  const value = process.env.MINUSPOD_SESSION || fromFile;
  if (!value) throw new Error('set MINUSPOD_SESSION to the session cookie value, or MINUSPOD_COOKIES to a cookies.txt path');
  return value;
}
// A logged-in dark-theme browser context sized for one device; `scale` 1 gives CSS pixels for headless screenshots.
export async function openContext(browser, T, device, { scale = DEVICES[device].DSF } = {}) {
  const DEV = DEVICES[device];
  const ctx = await browser.newContext({ viewport: { width: DEV.W / DEV.DSF, height: DEV.H / DEV.DSF }, deviceScaleFactor: scale, isMobile: device === 'phone', hasTouch: device === 'phone', colorScheme: 'dark' });
  await ctx.addCookies([{ name: 'session', value: sessionCookie(), domain: T.cookieDomain, path: '/', httpOnly: true, secure: true }]);
  await ctx.addInitScript(() => { localStorage.setItem('theme', 'dark'); if (!localStorage.getItem('themeId')) localStorage.setItem('themeId', 'slate'); });
  return ctx;
}
// Phone takes get the app's touch-target sizes; every take loses the text caret.
export const PAGE_CSS = {
  phone: `input, select, textarea { font-size: 16px; }
  button, summary, [role="switch"], .touch-target { min-width: 44px; min-height: 44px; }
  @media (max-width: 639.98px) { .touch-target-row { min-width: 0; } .touch-compact { min-width: 36px; min-height: 36px; } }
  input:not([type="checkbox"]):not([type="radio"]):not([type="range"]), select { min-height: 44px; }
  summary:not(.flex) { padding-block: 12px; }
  * { caret-color: transparent !important; }`,
  other: '* { caret-color: transparent !important; }',
};

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export async function smoothScroll(page, px, ms) {
  await page.evaluate(([px, ms]) => new Promise((done) => {
    const start = window.scrollY, t0 = performance.now();
    const ease = (t) => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    const step = (now) => { const t = Math.min(1, (now - t0) / ms); window.scrollTo(0, start + px * ease(t)); if (t < 1) requestAnimationFrame(step); else done(); };
    requestAnimationFrame(step);
  }), [px, ms]);
}
// Scroll so that `locator` sits `offset` px below the top of the viewport; jumpTo does it off camera, in one step.
export async function scrollTo(page, locator, offset = 120, ms = 1200) {
  const y = await locator.evaluate((el) => el.getBoundingClientRect().top);
  await smoothScroll(page, y - offset, ms);
}
export const jumpTo = (page, locator, offset = 120) => scrollTo(page, locator, offset, 10);
export async function typeSlow(locator, text, delay = 70) { await locator.click(); for (const ch of text) { await locator.pressSequentially(ch, { delay: 0 }); await sleep(delay); } }
// Open a collapsible settings or feed section by its header text and return the header.
export async function expandSection(p, re) {
  const h = p.getByRole('button', { name: re }).first(); await h.scrollIntoViewIfNeeded();
  if ((await h.getAttribute('aria-expanded')) === 'false') await h.click();
  await sleep(800); return h;
}
// Settings page: fold the Notifications group so webhook endpoints stay off screen.
export async function collapseNotifications(p) {
  for (const name of [/^Webhooks/, /^Email/, /^Notifications/]) { const h = p.getByRole('button', { name }).first(); if (await h.count() && (await h.getAttribute('aria-expanded')) === 'true') { await h.click(); await sleep(300); } }
}
// Expand a section and park its header `offset` px from the top; `settings` first folds the Notifications group.
export async function openSection(p, re, offset = 130, { settings = false } = {}) {
  if (settings) await collapseNotifications(p);
  const h = await expandSection(p, re); await jumpTo(p, h, offset); return h;
}

// Every rendered text node (a dropdown's selected value included) and input value that matches the sensitive pattern, or any URL in an input.
// Returns the elements to hide, each lifted to the smallest ancestor that has other content (the row or block the hit sits in).
const SENSITIVE_HITS = ({ pattern, hide }) => {
  const RE = new RegExp(pattern, 'i'), hits = [];
  const visible = (e) => { for (; e; e = e.parentElement) { if (e.tagName === 'OPTION') { if (!e.selected) return false; continue; } if (/^(SCRIPT|STYLE|NOSCRIPT)$/.test(e.tagName)) return false; const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || cs.display === 'none') return false; } return true; };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) if (RE.test(n.textContent) && visible(n.parentElement)) hits.push([n.parentElement, n.textContent]);
  document.querySelectorAll('input, textarea').forEach((i) => { if (i.type !== 'hidden' && visible(i) && (i.type === 'password' || i.type === 'url' || /https?:\/\//.test(i.value) || RE.test(i.value))) hits.push([i, i.value]); });
  // A text hit hides its row; an input hit climbs to the labelled row around it (the "Feed URL:" line, a settings field).
  const hasText = (el) => { const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let t; while ((t = tw.nextNode())) if (t.textContent.trim() && !t.parentElement.closest('svg')) return true; return false; };
  if (hide) hits.forEach(([el, t]) => {
    if (el.tagName === 'OPTION') el = el.closest('select');  // the painted value belongs to the select
    // A bare URL (a feed link, a webhook endpoint) takes its whole line, label and copy button included.
    let box = el.closest('tr, li, [role="row"], [role="listitem"], label') || (/^\s*https?:\/\//.test(t) && el.parentElement) || el;
    const up = el.matches('input, textarea') ? (b) => !hasText(b) : (b) => b.parentElement.children.length === 1;
    while (box.parentElement && box.parentElement !== document.body && up(box)) box = box.parentElement;
    box.style.visibility = 'hidden';
  });
  return hits.map(([, t]) => t.trim().slice(0, 60));
};
export async function hideSensitive(page, T) { await page.evaluate(SENSITIVE_HITS, { pattern: T.sensitive, hide: true }); }
// The same walk after hiding: anything still rendered that matches is a leak, and the take is refused.
export async function leakCheck(page, T, name) {
  const hits = await page.evaluate(SENSITIVE_HITS, { pattern: T.sensitive, hide: false });
  if (hits.length) throw new Error(`LEAK in ${name}: ${hits[0]}`);
}
// Resolve once a waveform canvas inside the open dialog has drawn real peaks, not a blank or loading box.
export async function waitForWaveform(p, timeout = 60000) {
  await p.waitForFunction(() => {
    const dlg = document.querySelector('[role="dialog"]') || document.body;
    if (/Loading waveform/.test(dlg.innerText)) return false;
    // wavesurfer renders inside a shadow root, so walk those too.
    const canvases = []; const walk = (root) => { root.querySelectorAll('*').forEach((el) => { if (el.tagName === 'CANVAS') canvases.push(el); if (el.shadowRoot) walk(el.shadowRoot); }); }; walk(dlg);
    return canvases.some((c) => {
      if (c.width < 50 || c.height < 100) return false;  // the main waveform, not the window minimap
      const ctx = c.getContext('2d'); if (!ctx) return false;
      const row = ctx.getImageData(0, Math.floor(c.height / 2), c.width, 1).data;
      let lit = 0; for (let i = 3; i < row.length; i += 4) if (row[i] > 0) lit++;
      return lit > c.width * 0.05;
    });
  }, null, { timeout });
  await sleep(300);
}

// Keeps hiding and checking while a take runs, so a dialog or toast opened mid-beat is caught too. Returns a stop() that rethrows a leak.
export function guard(page, T, name, every = 400) {
  let error = null, busy = false;
  const timer = setInterval(async () => {
    if (busy || error) return; busy = true;
    try { await hideSensitive(page, T); await leakCheck(page, T, name); } catch (e) { if (!/Target closed|Execution context was destroyed|navigation/i.test(String(e))) error = e; } finally { busy = false; }
  }, every);
  return async () => { clearInterval(timer); while (busy) await sleep(50); if (error) throw error; };
}
