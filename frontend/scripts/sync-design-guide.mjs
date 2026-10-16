import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const root = new URL('../../', import.meta.url);
const guidePath = new URL('design-guide/MinusPod Design System.dc.html', root);
const source = fs.readFileSync(new URL('frontend/src/themes/themes.ts', root), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled.replaceAll('import.meta.env.DEV', 'false'), context);
const { THEMES, THEME_VAR_KEYS } = context.exports;
const css = fs.readFileSync(new URL('frontend/src/index.css', root), 'utf8');
function variables(selector) {
  const block = css.slice(css.indexOf(selector + ' {'));
  const body = block.slice(block.indexOf('{') + 1, block.indexOf('}'));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
}
const defaults = { light: variables(':root'), dark: variables('.dark') };
const modeVars = mode => Object.fromEntries(THEME_VAR_KEYS.map(key => [key, defaults[mode][key]]));
const block = `  // BEGIN GENERATED THEMES - npm run design:sync\n  static SLATE_LIGHT = ${JSON.stringify(modeVars('light'), null, 2)};\n  static SLATE_DARK = ${JSON.stringify(modeVars('dark'), null, 2)};\n  static THEMES = ${JSON.stringify(THEMES, null, 2)};\n  // END GENERATED THEMES\n\n`;
const original = fs.readFileSync(guidePath, 'utf8');
let next = original.replace(/  (?:\/\/ BEGIN GENERATED THEMES[^\n]*\n  )?static SLATE_LIGHT = [\s\S]*?(?=  renderVals\(\))/, block);
const defaultCss = THEME_VAR_KEYS.map(key => `--${key}:${defaults.light[key]};`).join('\n');
next = next.replace(/:root\{[\s\S]*?\n\}/, `:root{\n${defaultCss}\n--radius:0.5rem;\n}`);
next = next.replace(/data-props="([^"]+)"/, (_, encoded) => {
  const props = JSON.parse(encoded.replaceAll('&quot;', '"'));
  props.theme.options = THEMES.map(theme => theme.id);
  return `data-props="${JSON.stringify(props).replaceAll('"', '&quot;')}"`;
});
if (process.argv.includes('--check')) {
  if (next !== original) {
    console.error('Design guide tokens differ from the app. Run npm run design:sync.');
    process.exitCode = 1;
  }
} else {
  fs.writeFileSync(guidePath, next);
}
