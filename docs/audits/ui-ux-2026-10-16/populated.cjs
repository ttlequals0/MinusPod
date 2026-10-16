const { chromium } = require('../../../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const out=__dirname;
const phase=process.argv.includes('--after')?'after':'before';
const scenarios=[
  ['dashboard',''], ['feed','feeds/daily-tech-news-show'],
  ['episode','feeds/daily-tech-news-show/episodes/6b39eef6285f'],
  ['patterns','patterns'], ['detections','patterns?tab=detected-ads'],
  ['review','patterns?tab=ad-review'], ['cleanup','patterns?tab=cleanup'],
  ['sponsors','sponsors'], ['history','history'],
  ['downloads','history?tab=downloads'], ['stats','stats'],
  ['queue','queue'], ['settings','settings'], ['search','search?q=technology'],
];
const only=process.argv.find(a=>a.startsWith('--only='))?.slice(7).split(',');
const clean=text=>text.replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g,'[redacted IP]');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const evidencePath=path.join(out,`populated-${phase}.json`);
  const results=only&&fs.existsSync(evidencePath)?JSON.parse(fs.readFileSync(evidencePath,'utf8')).filter(r=>!only.includes(r.name)):[];
  try {
    for(const width of [390,768,1440]) {
      const context=await browser.newContext({viewport:{width,height:width===390?844:1000},hasTouch:width<1000,isMobile:width===390,deviceScaleFactor:1});
      await context.addInitScript(()=>{localStorage.setItem('theme','light');localStorage.setItem('themeId','slate');});
      await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
      for(const [name,route] of scenarios.filter(([name])=>!only||only.includes(name))) {
        const page=await context.newPage();
        const errors=[],failures=[];
        page.on('pageerror',e=>errors.push(e.message));
        page.on('response',r=>{if(r.status()>=400) failures.push({path:new URL(r.url()).pathname,status:r.status()});});
        await page.goto('http://127.0.0.1:5173/ui/'+route);
        await page.waitForTimeout(3500);
        const client=await context.newCDPSession(page);
        await client.send('Emulation.setTouchEmulationEnabled',{enabled:false});
        await client.send('Emulation.setTouchEmulationEnabled',{enabled:width<1000});
        await page.waitForTimeout(250);
        if(name==='downloads') await page.locator('main td').evaluateAll(cells=>cells.forEach(cell=>{
          if(/\b(?:\d{1,3}\.){3}\d{1,3}\b/.test(cell.textContent)) cell.style.filter='blur(7px)';
        }));
        const state=await page.evaluate(()=>({
          viewport:{width:innerWidth,height:innerHeight,coarse:matchMedia('(pointer:coarse)').matches},
          text:document.querySelector('main')?.innerText,
          scrollWidth:document.documentElement.scrollWidth,
          horizontalRegions:[...document.querySelectorAll('main *')].filter(e=>e.checkVisibility()&&e.scrollWidth>e.clientWidth+2&&['auto','scroll'].includes(getComputedStyle(e).overflowX)).map(e=>({tag:e.tagName,width:e.clientWidth,scroll:e.scrollWidth,text:e.textContent.slice(0,160)})),
          headings:[...document.querySelectorAll('main h1,main h2')].map(e=>e.textContent),
          smallControls:[...document.querySelectorAll('main button,main input,main select,main summary')].filter(e=>e.checkVisibility()).map(e=>({name:(e.getAttribute('aria-label')||e.textContent||e.getAttribute('placeholder')||e.tagName).trim().slice(0,90),height:e.getBoundingClientRect().height,width:e.getBoundingClientRect().width})).filter(c=>c.height<44||c.width<44),
        }));
        state.text=clean(state.text||'');
        await page.screenshot({path:path.join(out,'images',`data-${name}-${width}-${phase}.png`)});
        if(['feed','episode','settings'].includes(name)&&width!==768) await page.screenshot({path:path.join(out,'images',`data-${name}-${width}-${phase}-full.png`),fullPage:true});
        results.push({name,width,state,errors,failures});
        fs.writeFileSync(path.join(out,`populated-${phase}.json`),JSON.stringify(results,null,2));
        console.log(`${name} ${width}px: ${state.headings.join(' / ')}; document ${state.scrollWidth}px; ${errors.length} errors`);
        await page.close();
      }
      await context.close();
    }
  } finally {await browser.close();}
})();
