const { chromium } = require('../../../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  try {
    for (const width of (process.argv.includes('--draft-only') ? [] : [390, 1440])) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, hasTouch: width === 390 });
      await context.route('**/*', route => new URL(route.request().url()).pathname.startsWith('/api/') ? route.abort() : route.continue());
      await context.addInitScript(() => { localStorage.setItem('theme', 'light'); localStorage.setItem('themeId', 'slate'); });
      const page = await context.newPage();
      for (const name of ['add', 'import', 'queue', 'settings']) {
        await page.goto('http://127.0.0.1:5173/ui/' + (name === 'import' ? 'patterns' : name));
        if (name === 'import') await page.getByRole('button', { name: 'Import', exact: true }).click();
        else if (name === 'settings') await page.getByRole('button', { name: 'Retry', exact: true }).waitFor({ timeout: 20000 });
        else await page.waitForTimeout(4000);
        await page.screenshot({ path: path.join(__dirname, 'images', `implemented-${name}-${width}-after.png`) });
      }
      await context.close();
    }
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:5173/ui/sponsors');
    await page.getByRole('button', { name: '+ Add Sponsor', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Add Sponsor', exact: true });
    const actual = await (await context.request.get('http://127.0.0.1:5173/api/v1/sponsors')).json();
    const name = (Array.isArray(actual) ? actual : actual.sponsors)[0].name;
    const input = dialog.locator('input[type="text"]').first();
    await input.fill(name);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    const confirm = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
    await confirm.waitFor();
    await page.screenshot({ path: path.join(__dirname, 'images', 'implemented-discard-390-after.png') });
    await confirm.getByRole('button', { name: 'Cancel', exact: true }).click();
    if (await input.inputValue() !== name) throw Error('Draft lost after cancelling discard');
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    if (await page.getByRole('dialog').count()) throw Error('Discard left a dialog open');
    if (await page.locator('[inert]').count()) throw Error('Discard left the page inert');
    if (!(await page.getByRole('button', { name: '+ Add Sponsor', exact: true }).evaluate(el => el === document.activeElement))) throw Error('Discard did not restore the trigger');
    fs.writeFileSync(path.join(__dirname, 'implemented-interactions.json'), JSON.stringify({ sponsorDraftCancelPreserved: true, explicitDiscardCloses: true, backgroundRestored: true, triggerFocusRestored: true, pageErrors: errors }, null, 2));
  } finally { await browser.close(); }
})();
