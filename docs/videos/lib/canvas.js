// Shared frame contract for the animated videos. Each animation's index.html loads this, then defines its own
// episode constants, scene-specific drawing (tape, rail) and the scenes list, and ends with finish(drawRail).
// Scenes draw in portrait coordinates (W x H). The landscape layout places the caption and rail in a left column
// and scales the portrait content box into the right side of a 1920x1080 frame.
const W = 1080, H = 1920, FPS = 30;
const LANDSCAPE = new URLSearchParams(location.search).get('layout') === 'landscape';
const OUT_W = LANDSCAPE ? 1920 : W, OUT_H = LANDSCAPE ? 1080 : H;
const BOX = { top: 520, bottom: 1700 };                      // portrait y range that holds every scene's content
const LS = { capX: 72, capW: 760, scale: (OUT_H - 80) / (BOX.bottom - BOX.top) };
LS.x = OUT_W - 72 - W * LS.scale;
const C = {
  bg: '#15181D', fg: '#F3F5F7', muted: '#9AA3AD', dim: '#5F6873', border: '#3A4049', card: '#1F242B', card2: '#272D35',
  primary: '#5ABFDD', ad: '#EF5F5D', ok: '#05DF73', warn: '#FFB900', teal: '#00C9A7', purple: '#C084FC',
};
// One content column shared by the tape, cards and gate; colX maps an episode fraction into it.
const COL_W = 880, COL_X = (W - COL_W) / 2, colX = (f) => COL_X + f * COL_W;
const span = (ads) => ads.reduce((a, [s, e]) => a + (e - s), 0);
const adName = ([a, b]) => (a === 0 ? 'pre-roll' : b === 1 ? 'post-roll' : 'mid-roll');

const cv = document.getElementById('c'); cv.width = OUT_W; cv.height = OUT_H; const ctx = cv.getContext('2d');
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const seg = (t, a, d) => clamp01((t - a) / d);
const eio = (p) => (p < 0.5 ? 2 * p * p : -1 + (4 - 2 * p) * p);
const eo = (p) => 1 - Math.pow(1 - p, 3);
const lerp = (a, b, p) => a + (b - a) * p;
const font = (w, s) => `${w} ${s}px Roboto`;
// Fade in over inDur from inStart, fade out over outDur before dur ends.
const envelope = (lt, dur, inStart, inDur, outDur) => Math.min(seg(lt, inStart, inDur), seg(dur - lt, 0, outDur));
const logo = new Image(); logo.src = '../../../frontend/public/logo-dark.svg';
window.ready = Promise.all([document.fonts.load(font(700, 64)), document.fonts.load(font(400, 38)), document.fonts.load(font(500, 30)),
  new Promise((r, j) => { logo.onload = r; logo.onerror = () => j(new Error('logo failed to load: ' + logo.src)); if (logo.complete) r(); })]);

function rr(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); }
function fillRR(x, y, w, h, r, color) { rr(x, y, w, h, r); ctx.fillStyle = color; ctx.fill(); }
function strokeRR(x, y, w, h, r, color, lw = 2) { rr(x, y, w, h, r); ctx.strokeStyle = color; ctx.lineWidth = lw; ctx.stroke(); }
// Draw fn at opacity p, slid up by dy while it fades in.
function reveal(p, fn, dy = 0) { if (p <= 0) return; ctx.save(); ctx.globalAlpha *= p; ctx.translate(0, (1 - p) * dy); fn(); ctx.restore(); }
function wrap(text, f, maxw) {
  ctx.font = f; const words = text.split(' '), lines = []; let cur = '';
  for (const w of words) { const t = (cur + ' ' + w).trim(); if (ctx.measureText(t).width <= maxw) cur = t; else { lines.push(cur); cur = w; } }
  if (cur) lines.push(cur); return lines;
}
function text(s, x, y, f, color, align = 'left', alpha = 1) {
  ctx.save(); ctx.globalAlpha *= alpha; ctx.font = f; ctx.fillStyle = color; ctx.textAlign = align; ctx.textBaseline = 'alphabetic'; ctx.fillText(s, x, y); ctx.restore();
}
// Centered footnote, wrapped to the caption width.
function note(str, y, alpha, color = C.muted, size = 28) { reveal(alpha, () => wrap(str, font(400, size), W - 144).forEach((l, i) => text(l, W / 2, y + i * (size + 10), font(400, size), color, 'center'))); }
function pillWidth(label, size = 26, weight = 500) { ctx.font = font(weight, size); return ctx.measureText(label).width + 36; }
function pill(label, x, y, color, { weight = 500, size = 26, align = 'left', filled = false } = {}) {
  const pw = pillWidth(label, size, weight), ph = size + 22;
  const px = align === 'center' ? x - pw / 2 : align === 'right' ? x - pw : x;
  ctx.save(); ctx.globalAlpha *= filled ? 1 : 0.18; fillRR(px, y, pw, ph, ph / 2, color); ctx.restore();
  strokeRR(px, y, pw, ph, ph / 2, color, 1.5);
  text(label, px + pw / 2, y + ph / 2 + size * 0.36, font(weight, size), filled ? C.bg : color, 'center');
  return pw;
}
// Centered row of pills; items are [label, color, alpha].
function pillRow(items, y, gap = 24, size = 26) {
  const widths = items.map(([l]) => pillWidth(l, size)); let x = (W - widths.reduce((a, b) => a + b, 0) - gap * (items.length - 1)) / 2;
  items.forEach(([l, col, a], i) => { const px = x; reveal(a, () => pill(l, px, y, col, { size }), 10); x += widths[i] + gap; });
}
function arrow(x1, y1, x2, y2, color, p = 1, lw = 4) {
  if (p <= 0) return; const ex = lerp(x1, x2, p), ey = lerp(y1, y2, p);
  ctx.save(); ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = lw; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(ex, ey); ctx.stroke();
  if (p > 0.95) { const a = Math.atan2(y2 - y1, x2 - x1), s = 16; ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - s * Math.cos(a - 0.5), ey - s * Math.sin(a - 0.5)); ctx.lineTo(ex - s * Math.cos(a + 0.5), ey - s * Math.sin(a + 0.5)); ctx.closePath(); ctx.fill(); }
  ctx.restore();
}
function card(x, y, w, h, title, sub, accent, alpha = 1, r = 22) {
  reveal(alpha, () => {
    ctx.shadowColor = 'rgba(0,0,0,0.45)'; ctx.shadowBlur = 30; ctx.shadowOffsetY = 10; fillRR(x, y, w, h, r, C.card); ctx.shadowColor = 'transparent';
    strokeRR(x, y, w, h, r, C.border, 1.5);
    if (accent) { ctx.save(); rr(x, y, w, h, r); ctx.clip(); ctx.fillStyle = accent; ctx.fillRect(x, y, 8, h); ctx.restore(); }
    if (title) text(title, x + 34, y + (sub ? 56 : h / 2 + 11), font(700, 32), C.fg);
    if (sub) for (const [i, l] of wrap(sub, font(400, 26), w - 60).entries()) text(l, x + 34, y + 98 + i * 34, font(400, 26), C.muted);
  });
}
// Numbered checklist; done(i) returns 0..1 progress of item i.
function checklist(x, y, items, done, rowH = 86) {
  items.forEach((label, i) => {
    const p = done(i), yy = y + i * rowH, col = p >= 1 ? C.ok : p > 0 ? C.primary : C.dim;
    ctx.save(); ctx.strokeStyle = col; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(x + 22, yy, 20, 0, Math.PI * 2); ctx.stroke();
    if (p >= 1) { ctx.fillStyle = C.ok; ctx.beginPath(); ctx.arc(x + 22, yy, 12, 0, Math.PI * 2); ctx.fill(); }
    else if (p > 0) { ctx.fillStyle = C.primary; ctx.beginPath(); ctx.moveTo(x + 22, yy); ctx.arc(x + 22, yy, 12, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p); ctx.closePath(); ctx.fill(); }
    ctx.restore();
    text(label, x + 64, yy + 10, font(p > 0 ? 700 : 500, 30), p > 0 ? C.fg : C.dim);
  });
}
// Logo card for the opening and closing scenes; draw() adds the lines under the logo.
function splash(lt, d, draw) {
  const z = 1 + 0.03 * (lt / d);
  reveal(eo(seg(lt, 0.1, 0.8)), () => {
    ctx.translate(OUT_W / 2, LANDSCAPE ? 520 : 860); ctx.scale(z, z);
    const lw = 560, lh = lw * logo.height / logo.width; ctx.drawImage(logo, -lw / 2, -lh - 40, lw, lh);
    draw();
  });
}
function caption(head, sub, p) {
  const [x, maxw, hs, ss] = LANDSCAPE ? [LS.capX, LS.capW, 56, 34] : [72, W - 144, 62, 38];
  reveal(p, () => {
    let y = LANDSCAPE ? 150 : 170;
    for (const l of wrap(head, font(700, hs), maxw)) { text(l, x, y, font(700, hs), C.fg); y += hs * 1.2; }
    if (sub) { y += 8; for (const l of wrap(sub, font(400, ss), maxw)) { text(l, x, y, font(400, ss), C.muted); y += ss * 1.27; } }
  }, 18);
}

// Scenes: duration, headline, subline, draw(localTime, duration), plus one page-defined field the rail reads.
const scenes = [];
const add = (dur, head, sub, draw, rail) => scenes.push({ dur, head, sub, draw, rail });
// Publishes the frame contract render.mjs drives. drawRail(scene, alpha) draws the page's footer rail.
function finish(drawRail) {
  window.W = OUT_W; window.H = OUT_H; window.FPS = FPS;
  window.total = scenes.reduce((a, s) => a + s.dur, 0);
  // Midpoint of each scene, the default preview set.
  window.sceneMids = scenes.map((s, i) => scenes.slice(0, i).reduce((a, x) => a + x.dur, 0) + s.dur / 2);
  window.render = function (t) {
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1; ctx.fillStyle = C.bg; ctx.fillRect(0, 0, OUT_W, OUT_H);
    let acc = 0, idx = 0; for (; idx < scenes.length - 1 && t >= acc + scenes[idx].dur; idx++) acc += scenes[idx].dur;
    const sc = scenes[idx], lt = Math.min(t - acc, sc.dur), last = idx === scenes.length - 1;
    const fade = last ? seg(lt, 0, 0.35) : envelope(lt, sc.dur, 0, 0.35, 0.35);
    reveal(fade, () => { if (LANDSCAPE && sc.head) { ctx.translate(LS.x, 40); ctx.scale(LS.scale, LS.scale); ctx.translate(0, -BOX.top); } sc.draw(lt, sc.dur); });
    if (sc.head) caption(sc.head, sc.sub, envelope(lt, sc.dur, 0.15, 0.45, 0.35));
    if (sc.rail) drawRail(sc, fade);
  };
}
