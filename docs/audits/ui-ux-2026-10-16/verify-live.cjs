// Run from frontend against disposable local instances; never production.
const { chromium, webkit, expect } = require(process.cwd() + '/node_modules/@playwright/test');
const fs = require('node:fs');
const live = process.env.MINUSPOD_LIVE_URL;
const readonly = process.env.MINUSPOD_READONLY_URL;
for (const url of [live, readonly]) {
  if (!url || !['127.0.0.1', 'localhost'].includes(new URL(url).hostname)) throw new Error('Set local disposable instance URLs ending in /ui');
}
const checks = [];
(async () => {
  for (const [engine, browserType] of [['chromium', chromium], ['webkit', webkit]]) {
    const browser = await browserType.launch(engine === 'chromium' ? { channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' } : {});
    for (const width of [390, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 }, hasTouch: width < 768 });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      const record = (check) => { checks.push({ engine, width, check, passed: true }); console.log(`${engine} ${width}px: ${check}`); };
      await page.goto(live + '/settings');
      await page.getByRole('button', { name: 'Expand all', exact: true }).click();
      await expect(page.locator('#email-smtp-host')).toBeVisible();
      for (const id of ['email-smtp-host', 'db-backup-keep', 'cron', 'pattern-cleanup-batch', 'podping-node-0', 'modelPricing-0-input', 'windowSizeSeconds', 'softTimeoutMinutes', 'downloadUserAgent']) {
        const input = page.locator(`#${id}`);
        await input.scrollIntoViewIfNeeded();
        const original = await input.inputValue();
        const numeric = await input.getAttribute('type') === 'number';
        const changed = numeric ? String(Number(original || 0) + 1) : original + ' ';
        await input.fill(changed);
        await page.getByRole('link', { name: 'MinusPod', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
        await expect(dialog).toBeVisible();
        await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(input).toHaveValue(changed);
        await input.fill(original);
        await input.blur();
        record(`preserves ${id} on cancelled navigation`);
      }
      const timezone = page.locator('#notification-timezone');
      const originalZone = await timezone.inputValue();
      const otherZone = originalZone === 'UTC' ? 'America/New_York' : 'UTC';
      for (const zone of [otherZone, originalZone]) {
        await timezone.selectOption(zone);
        const saved = page.waitForResponse((r) => r.url().endsWith('/settings/notifications/timezone') && r.request().method() === 'PUT');
        await page.getByRole('button', { name: 'Save timezone', exact: true }).click();
        expect((await saved).ok()).toBe(true);
        await expect(page.getByRole('button', { name: 'Save timezone', exact: true })).toBeDisabled();
        const persisted = await page.request.get(new URL('/api/v1/settings/notifications/timezone', live).href);
        expect((await persisted.json()).timezone).toBe(zone);
      }
      record('timezone saves, persists, restores, and becomes clean');
      await page.goto(readonly + '/settings');
      await page.getByRole('button', { name: 'Expand all', exact: true }).click();
      const retention = page.locator('#download-history-retention');
      const originalDays = await retention.inputValue();
      const changedDays = String(Number(originalDays) + 1);
      await retention.scrollIntoViewIfNeeded();
      await retention.fill(changedDays);
      await page.waitForTimeout(100);
      await retention.blur();
      await expect(page.locator('#download-setting-error')).toBeVisible({timeout:15000});
      await expect(retention).toHaveValue(changedDays);
      await page.getByRole('link', { name: 'MinusPod', exact: true }).click();
      const discard = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
      await expect(discard).toBeVisible();
      await discard.getByRole('button', { name: 'Cancel', exact: true }).click();
      await retention.fill(originalDays);
      await retention.blur();
      record('failed retention save retains value, announced error, and navigation guard');
      await context.close();
    }
    await browser.close();
  }
  fs.writeFileSync(process.env.MINUSPOD_LIVE_EVIDENCE || '/private/tmp/minuspod-live-verification.json', JSON.stringify({ checks }, null, 2) + '\n');
})().catch((error) => { console.error(error); process.exit(1); });
