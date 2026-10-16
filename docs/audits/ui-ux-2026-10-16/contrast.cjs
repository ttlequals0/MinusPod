const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../../frontend/node_modules/typescript');

const root = path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(root, 'frontend/src/themes/themes.ts'), 'utf8').replaceAll('import.meta.env.DEV', 'false');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const mod = { exports: {} };
new Function('exports', 'module', compiled)(mod.exports, mod);
const css = fs.readFileSync(path.join(root, 'frontend/src/index.css'), 'utf8');
const vars = selector => {
  const block = css.slice(css.indexOf(`${selector} {`));
  return Object.fromEntries([...block.slice(block.indexOf('{') + 1, block.indexOf('}')).matchAll(/--([\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
};
const defaults = { light: vars(':root'), dark: vars('.dark') };
const rgb = value => {
  const [h, s, l] = value.split(/\s+/).map(parseFloat);
  const a = (s / 100) * Math.min(l / 100, 1 - l / 100);
  return [0, 8, 4].map(n => {
    const k = (n + h / 30) % 12;
    return l / 100 - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  });
};
const lum = c => c.map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
const pairs = [
  ['primary-foreground', 'primary'], ['destructive-foreground', 'destructive'],
  ['foreground', 'background'], ['foreground', 'card'], ['card-foreground', 'card'],
  ['muted-foreground', 'background'], ['muted-foreground', 'card'],
  ['primary', 'background'], ['primary', 'card'], ['secondary-foreground', 'secondary'],
];
const results = [];
for (const theme of mod.exports.THEMES) {
  for (const mode of ['light', 'dark']) {
    const v = { ...defaults[mode], ...theme[mode] };
    for (const [fg, bg] of pairs) results.push({ theme: theme.id, mode, fg, bg, ratio: ratio(rgb(v[fg]), rgb(v[bg])), passesNormalText: ratio(rgb(v[fg]), rgb(v[bg])) >= 4.5 });
  }
}
fs.writeFileSync(path.join(__dirname, process.argv.includes('--after') ? 'contrast-after.json' : 'contrast-evidence.json'), JSON.stringify({ method: 'WCAG sRGB relative luminance; normal text threshold 4.5:1; unrounded ratios used for decisions; base colors only, no hover/opacity compositing', themeCount: mod.exports.THEMES.length, results }, null, 2));
console.log(JSON.stringify({ themes: mod.exports.THEMES.length, pairChecks: results.length, failures: results.filter(x => !x.passesNormalText).length, slate: results.filter(x => x.theme === 'slate') }, null, 2));
