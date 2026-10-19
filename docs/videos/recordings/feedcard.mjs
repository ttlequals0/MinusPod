// Renders a served RSS feed as a code card and loops it into a laptop-sized clip, for the Podcasting 2.0 video.
// Usage: node feedcard.mjs <feed.xml> [clips/p20/feedxml.mp4]
// Every URL loses its query string and every host except the namespace hosts becomes example.com, whatever the
// parameter is called, so a feed key or token cannot reach the card.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { DEVICES } from './helpers.mjs';

const [,, xmlFile, outFile = 'clips/p20/feedxml.mp4'] = process.argv;
if (!xmlFile) { console.error('usage: node feedcard.mjs <feed.xml> [out.mp4]'); process.exit(2); }
const esc = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// Whole-line XML comments (the renderer's version stamp) are left out of the card.
let xml = fs.readFileSync(xmlFile, 'utf8').split('\n').filter((l) => !(l.trim().startsWith('<!--') && l.trim().endsWith('-->'))).join('\n');
const NAMESPACE_HOSTS = /^https?:\/\/(www\.)?(itunes\.com|podcastindex\.org|w3\.org|purl\.org)$/i;
xml = xml.replace(/https?:\/\/[^/\s"'<]+/g, (m) => (NAMESPACE_HOSTS.test(m) ? m : 'https://example.com')).replace(/\?[^"'\s<]*/g, '');
const lines = xml.trim().split('\n').map((l) => esc(l)
  .replace(/(&lt;\/?)(podcast:[a-zA-Z]+)/g, '$1<b class="p2">$2</b>')
  .replace(/(&lt;\/?)([a-zA-Z]+(?::[a-zA-Z]+)?)(?=[ &>])/g, (m, a, tag) => tag.startsWith('podcast:') ? m : `${a}<span class="tag">${tag}</span>`)
  .replace(/(&quot;[^&]*&quot;)/g, '<span class="str">$1</span>'));
const page = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;500&family=Roboto:wght@500;700&display=swap">
<style>
html,body{margin:0;background:#15181D;color:#F3F5F7;width:1000px;height:900px;overflow:hidden}
.bar{height:64px;display:flex;align-items:center;padding:0 28px;background:#1F242B;border-bottom:1px solid #3A4049;font:500 20px Roboto;color:#9AA3AD}
.bar b{color:#F3F5F7;font-weight:700;margin-right:14px}
pre{margin:0;padding:26px 30px;font:400 18.5px/1.55 'Roboto Mono';color:#C9D1D9;white-space:pre}
.tag{color:#5ABFDD} .p2{color:#00C9A7;font-weight:500} .str{color:#FFB900}
</style></head><body>
<div class="bar"><b>Served feed</b> rss.xml for the local feed, Podcasting 2.0 namespace</div>
<pre>${lines.join('\n')}</pre></body></html>`;
const html = path.resolve('work-feedcard.html'), png = path.resolve('work-feedcard.png');
fs.writeFileSync(html, page);
const { W, H, DSF } = DEVICES.laptop;
const browser = await chromium.launch({ channel: 'chromium' });
const p = await browser.newPage({ viewport: { width: W / DSF, height: H / DSF }, deviceScaleFactor: DSF });
await p.goto(pathToFileURL(html).href); await p.waitForTimeout(1500); await p.screenshot({ path: png }); await browser.close();
fs.mkdirSync(path.dirname(outFile), { recursive: true });
const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-loop', '1', '-framerate', '30', '-i', png, '-t', '8', '-vf', 'format=yuv420p', '-c:v', 'libx264', '-crf', '12', outFile], { stdio: 'inherit' });
fs.rmSync(html); fs.rmSync(png);
if (r.status) process.exit(r.status);
console.log('wrote', outFile, `${W}x${H}, 8 s`);
