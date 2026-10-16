const { chromium } = require('../../../frontend/node_modules/@playwright/test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    await page.goto(pathToFileURL(path.join(__dirname,'gallery.html')).href);
    await page.locator('img').evaluateAll(images=>images.forEach(image=>image.loading='eager'));
    await page.waitForFunction(()=>[...document.images].every(image=>image.complete&&image.naturalWidth>0));
    assert.equal(await page.locator('section:visible').count(),12);
    assert.equal(await page.locator('img').count(),24);
    await page.getByRole('button',{name:'Phone',exact:true}).click();
    assert.equal(await page.locator('section:visible').count(),7);
    await page.getByRole('button',{name:'Desktop',exact:true}).click();
    assert.equal(await page.locator('section:visible').count(),5);
    await page.getByRole('button',{name:'All comparisons'}).click();
    assert.equal(await page.locator('section:visible').count(),12);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
    console.log('Gallery PASS: 12 comparisons, 24 images, Phone/Desktop filters, no overflow at 390 px.');
  } finally { await browser.close(); }
})();
