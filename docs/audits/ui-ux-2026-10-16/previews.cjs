const { chromium } = require('../../../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const out = __dirname;

async function commonPreview(page) {
  await page.addStyleTag({ content: `
    :root { --primary:194 66% 32%; --muted-foreground:210 5% 41%; --destructive:1 82% 47%; }
    .dark { --primary:194 66% 61%; --muted-foreground:0 0% 72%; --destructive:1 82% 65%; --destructive-foreground:213 10% 17%; }
    .audit-no-activity + header { top:0!important; }
    .audit-no-activity { display:none!important; }
    .audit-error { border:1px solid hsl(var(--destructive)/.35); background:hsl(var(--card)); border-radius:8px; padding:20px; color:hsl(var(--foreground)); }
    .audit-error h2 { font-size:18px; font-weight:600; margin-bottom:8px; }
    .audit-error p { font-size:14px; margin-bottom:16px; }
    .audit-retry { background:hsl(var(--primary));color:hsl(var(--primary-foreground));min-height:44px;padding:8px 16px;border-radius:4px;font-size:14px; }
    @media(pointer:coarse) { main button, main summary, main input:not([type=file]), main select { min-height:44px; } main summary { display:flex;align-items:center;flex-wrap:wrap;column-gap:4px; } }
    [role=dialog] { max-height:calc(100dvh - 32px)!important; overflow-y:auto; }
  ` });
  await page.evaluate(() => {
    const spacer = document.querySelector('header')?.previousElementSibling;
    if (spacer?.getAttribute('aria-hidden') === 'true' && !document.querySelector('[data-viewport-inset="top"]')) spacer.classList.add('audit-no-activity');
    for (const a of document.querySelectorAll('nav a')) if (a.getAttribute('href') === location.pathname) a.setAttribute('aria-current', 'page');
  });
}

async function patchPreview(page, scenario) {
  await commonPreview(page);
  await page.evaluate((scenario) => {
    const main = document.querySelector('main');
    if (scenario === 'add') {
      const browse = [...main.querySelectorAll('button')].find(e=>e.textContent.trim()==='browse to select');
      browse.style.cssText='display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:8px 16px;border-radius:4px;background:hsl(var(--secondary));color:hsl(var(--secondary-foreground));margin-top:8px';
      main.querySelector('details summary').setAttribute('aria-label','Advanced feed options');
    }
    if (scenario === 'search') {
      const box=main.querySelector('input');
      box.id='audit-search';
      const label=document.createElement('label'); label.htmlFor=box.id; label.textContent='Search your library';
      label.style.cssText='display:block;font-size:14px;font-weight:500;margin-bottom:8px';
      box.parentElement.before(label);
      const filters=[...main.querySelectorAll('button')].find(e=>e.textContent.trim()==='All').parentElement;
      filters.classList.add('audit-search-filters');
      filters.setAttribute('role','group'); filters.setAttribute('aria-label','Result type');
      for(const b of filters.querySelectorAll('button')) b.setAttribute('aria-pressed',b.textContent.trim()==='All'?'true':'false');
      const rebuild=[...main.querySelectorAll('button')].find(e=>e.textContent.trim()==='Rebuild Index');
      const detail=document.createElement('details');
      detail.style.cssText='margin-top:32px;border-top:1px solid hsl(var(--border));padding-top:16px;font-size:14px';
      const summary=document.createElement('summary');summary.textContent='Search maintenance';summary.style.cssText='cursor:pointer;min-height:44px;display:flex;align-items:center;color:hsl(var(--muted-foreground))';
      detail.append(summary,rebuild);main.firstElementChild.append(detail);
    }
    if (scenario === 'import') {
      const dialog=document.querySelector('[role=dialog]');
      const h=dialog.querySelector('h2');h.id='audit-import-title';dialog.setAttribute('aria-labelledby',h.id);
      const f=dialog.querySelector('input[type=file]');f.id='audit-import-file';
      const label=document.createElement('label');label.htmlFor=f.id;label.textContent='Pattern JSON file';label.style.cssText='display:block;font-size:14px;font-weight:500;margin-bottom:8px';f.before(label);
      const sel=dialog.querySelector('select');sel.id='audit-import-mode';sel.previousElementSibling.htmlFor=sel.id;
      for(const b of dialog.querySelectorAll('button')) b.style.minHeight='44px';
      f.style.cssText='display:block;width:100%;min-height:44px;margin-bottom:20px;padding:8px 0;font-size:14px';
      dialog.style.padding='24px';
      dialog.lastElementChild.style.cssText='display:flex;justify-content:flex-end;gap:8px;padding-top:16px;margin-top:20px;border-top:1px solid hsl(var(--border))';
    }
    if (scenario === 'queue') {
      const p=[...main.querySelectorAll('p')].find(e=>e.textContent==='No episodes are processing right now.');
      p.textContent='Processing status is unavailable. The server did not return queue data.';
      p.className='text-sm text-foreground';
      p.parentElement.setAttribute('role','alert');
      const retry=document.createElement('button');retry.textContent='Retry queue status';retry.className='audit-retry';retry.style.marginTop='16px';p.after(retry);
    }
  },scenario);
  if (scenario==='search') await page.addStyleTag({content:'.audit-search-filters { display:flex; flex-wrap:wrap;overflow:visible!important;gap:8px } .audit-search-filters button { min-height:44px; } @media(max-width:639px){.audit-search-filters {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));} .audit-search-filters button{padding:8px 10px;flex:1 1 28%;}}'});
  await page.waitForTimeout(350);
}

async function inspectState(page) {
  return page.evaluate(() => ({
    viewport:{width:innerWidth,height:innerHeight,coarse:matchMedia('(pointer:coarse)').matches},
    text:document.body.innerText,
    controls:[...document.querySelectorAll('main button,main input,main select,main summary,[role=dialog] button')].filter(e=>e.checkVisibility()).map(e=>({name:(e.getAttribute('aria-label')||e.textContent||e.getAttribute('placeholder')||e.tagName).trim().slice(0,90),width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})),
    dialogNames:[...document.querySelectorAll('[role=dialog]')].map(e=>e.getAttribute('aria-label')||document.getElementById(e.getAttribute('aria-labelledby'))?.textContent||null),
    scrollWidth:document.documentElement.scrollWidth,
  }));
}

(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const only=process.argv.find(a=>a.startsWith('--only='))?.slice(7);
  const results=only ? JSON.parse(fs.readFileSync(path.join(out,'preview-evidence.json'),'utf8')).filter(r=>r.scenario!==only) : [];
  for(const width of [390,1440]) {
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000},hasTouch:width===390,isMobile:width===390,deviceScaleFactor:1,colorScheme:'light'});
    await context.addInitScript(()=>{localStorage.setItem('theme','light');localStorage.setItem('themeId','slate');});
    for(const scenario of ['add','search','import','queue'].filter(s=>!only||s===only)) {
      const page=await context.newPage();
      await page.goto('http://127.0.0.1:5173/ui/'+(scenario==='import'?'patterns':scenario));
      await page.waitForTimeout(12500);
      const client=await context.newCDPSession(page);
      await client.send('Emulation.setTouchEmulationEnabled',{enabled:false});
      await client.send('Emulation.setTouchEmulationEnabled',{enabled:width===390});
      if(width===390) await client.send('Emulation.setEmitTouchEventsForMouse',{enabled:true,configuration:'mobile'});
      await page.waitForTimeout(350);
      if (width===390 && !await page.evaluate(()=>matchMedia('(pointer:coarse)').matches)) throw new Error('Phone capture requires a coarse pointer');
      if(scenario==='import') await page.getByRole('button',{name:'Import',exact:true}).click();
      await page.locator('input').evaluateAll(es=>es.forEach(e=>e.blur()));
      const item={scenario,width,before:await inspectState(page)};
      await page.screenshot({path:path.join(out,'images',`${scenario}-${width}-before.png`)});
      await patchPreview(page,scenario);
      item.after=await inspectState(page);
      await page.screenshot({path:path.join(out,'images',`${scenario}-${width}-after.png`)});
      results.push(item); fs.writeFileSync(path.join(out,'preview-evidence.json'),JSON.stringify(results,null,2));
      console.log(`Captured ${scenario} at ${width} px; before and proposed after`);
      await page.close();
    }
    await context.close();
  }
  if (process.argv.includes('--skip-settings')) { await browser.close(); return; }
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
  const page=await context.newPage();
  const responses=[];page.on('response',r=>{if(r.url().includes('/api/v1/settings'))responses.push({url:r.url(),status:r.status()});});
  await page.goto('http://127.0.0.1:5173/ui/settings');
  await page.waitForTimeout(45000);
  fs.writeFileSync(path.join(out,'settings-failure-evidence.json'),JSON.stringify({responses,state:await inspectState(page)},null,2));
  await page.screenshot({path:path.join(out,'images','settings-settled-390-before.png')});
  await browser.close();
})();
