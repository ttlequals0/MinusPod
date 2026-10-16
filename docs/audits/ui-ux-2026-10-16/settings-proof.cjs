const { chromium }=require('../../../frontend/node_modules/@playwright/test');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1,colorScheme:'light'});
  const failures=[];const navigations=[];const samples=[];const start=Date.now();
  page.on('response',r=>{if(r.url().endsWith('/api/v1/settings')) failures.push({elapsedMs:Date.now()-start,status:r.status()});});
  page.on('framenavigated',f=>{if(f===page.mainFrame())navigations.push(f.url());});
  await page.goto('http://127.0.0.1:5173/ui/settings');
  for(let i=0;i<9;i++) { await page.waitForTimeout(5000); samples.push({elapsedMs:Date.now()-start,text:await page.locator('main').innerText()}); }
  await page.evaluate(()=>window.stop());
  for(const width of [390,1440]) {
    await page.setViewportSize({width,height:width===390?844:1000});
    await page.screenshot({path:path.join(__dirname,'images',`settings-${width}-before.png`)});
  }
  await page.evaluate(()=>{
    document.querySelector('main').innerHTML='<div style="max-width:768px;margin:auto"><h1 style="font-size:24px;font-weight:700;margin-bottom:24px">Settings</h1><section role="alert" style="border:1px solid hsl(var(--border));background:hsl(var(--card));border-radius:8px;padding:24px"><h2 style="font-size:18px;font-weight:600;margin-bottom:12px">Settings could not be loaded</h2><p style="font-size:14px;margin-bottom:20px">The server is unavailable. Editing will be available after your saved settings load.</p><button style="min-height:44px;padding:8px 16px;border-radius:4px;background:hsl(194 66% 32%);color:white;font-size:14px">Retry loading settings</button></section></div>';
  });
  for(const width of [390,1440]) {
    await page.setViewportSize({width,height:width===390?844:1000});
    await page.screenshot({path:path.join(__dirname,'images',`settings-${width}-after.png`)});
  }
  fs.writeFileSync(path.join(__dirname,'settings-proof.json'),JSON.stringify({failures,navigations,samples},null,2));
  console.log(JSON.stringify({settingsRequests:failures.length,navigations,sampleTextLengths:samples.map(s=>s.text.length)}));
  await browser.close();
})();
