// Cache-only browser verification. Usage: node tools/check_polish.mjs BUILD_DIRECTORY
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
const root=path.resolve(process.argv[2]),out=path.join(root,'_check/polish');
const tmp=process.env.TMPDIR||path.join(root,'_check/tmp');
fs.mkdirSync(out,{recursive:true});
fs.mkdirSync(tmp,{recursive:true});
const log=fs.openSync(path.join(out,'chromium.log'),'w');
const browser=spawn('/usr/bin/chromium',['--headless=new','--no-sandbox','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check','--disable-features=MediaRouter,OptimizationHints','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost','--remote-debugging-port=9345','--window-size=1920,1080','--user-data-dir='+path.join(tmp,'polish-chromium-profile'),'about:blank'],{env:{...process.env,TMPDIR:tmp},stdio:['ignore',log,log]});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let ws;
try {
  let tabs;
  for(let i=0;i<100;i++){try{tabs=await(await fetch('http://127.0.0.1:9345/json/list')).json();break;}catch{await pause(100);}}
  assert(tabs,'Chromium debugger did not start');
  ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise(r=>ws.addEventListener('open',r,{once:true}));
  let seq=0;const pending=new Map(),errors=[],requests=[],checks={screenshots:[]};
  ws.addEventListener('message',ev=>{const m=JSON.parse(ev.data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}}else{if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Runtime.consoleAPICalled'&&m.params.type==='error')errors.push(m.params.args);if(m.method==='Network.requestWillBeSent')requests.push(m.params.request.url);}});
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  async function wait(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await pause(100);}throw Error('Timeout: '+expression);}
  async function nav(hash){await call('Page.navigate',{url:'about:blank'});await wait('location.href==="about:blank"');await call('Page.navigate',{url:'file://'+root+'/app/index.html'+hash});await wait('window.GALLERY && document.getElementById("gCount").textContent.length>0');await pause(250);}
  async function shot(name,selector){await evaluate('document.getElementById("chartTooltip").hidden=true');
    if(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'})`);await pause(150);}
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    await evaluate('Promise.all([...document.images].filter(im=>{const r=im.getBoundingClientRect();return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight}).map(im=>im.decode().catch(()=>{})))');
    const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false}),png=Buffer.from(r.data,'base64');assert.equal(png.readUInt32BE(16),1920);assert.equal(png.readUInt32BE(20),1080);fs.writeFileSync(path.join(out,name+'.png'),png);checks.screenshots.push(name+'.png');
  }
  const key=async (key,code=key)=>{const vk={Enter:13,Tab:9,Escape:27,ArrowDown:40,ArrowUp:38}[key]||key.toUpperCase().charCodeAt(0);await call('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:vk,text:key==='Enter'?'\r':key.length===1?key:''});await call('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:vk});};
  await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
  await nav('#tokyo-night/mock?view=grid');
  await evaluate('localStorage.removeItem("omarchy-gallery:ui")');await nav('#tokyo-night/mock?view=grid');
  checks.default=await evaluate('({total:GALLERY.themes.length,count:document.getElementById("gCount").textContent,sort:document.getElementById("fSort").value,pinned:document.getElementById("builtinsToggle").textContent,expanded:document.getElementById("builtinsToggle").getAttribute("aria-expanded"),first:[...document.querySelectorAll(".card")].map(c=>({name:c.querySelector("b").textContent,score:c.querySelector(".rank-value")?.textContent})).slice(0,3),hud:document.getElementById("hudRank").textContent})');
  assert.equal(checks.default.expanded,'false');assert(checks.default.first[0].score.startsWith('#1 '));assert.equal(checks.default.hud,'Built into Omarchy');
  await shot('grid-default');await shot('builtins-collapsed');
  await evaluate('document.getElementById("builtinsToggle").click()');
  assert.equal(await evaluate('document.getElementById("builtinsToggle").getAttribute("aria-expanded")'),'true');
  assert.equal(await evaluate('[...document.querySelectorAll(".card")].filter(c=>GALLERY.themes[+c.dataset.i].builtin&&c.querySelector(".rank-value")).length'),0);
  await evaluate('document.getElementById("cards").scrollTop=0');await pause(100);await shot('builtins-expanded');
  await nav('#tokyo-night/mock?view=grid');assert.equal(await evaluate('document.getElementById("builtinsToggle").getAttribute("aria-expanded")'),'true');
  await evaluate('document.getElementById("builtinsToggle").click()');
  // Exercise the actual ranking menu with keyboard arrows, Enter and Escape.
  await evaluate('document.getElementById("fSort").click()');await key('ArrowDown');await key('Enter');
  assert.equal(await evaluate('document.getElementById("fSort").value'),'stars');
  checks.stars=await evaluate('({first:document.querySelector(".card b").textContent,score:document.querySelector(".rank-value").textContent,menu:document.getElementById("rankMenu").hidden,hash:location.hash})');
  const mostStars=await evaluate('Math.max(...GALLERY.themes.filter(t=>!t.builtin&&t.st).map(t=>t.st.s))');
  assert(checks.stars.score.includes('#1 '+mostStars+' stars'));assert(checks.stars.menu);await shot('grid-stars');
  await evaluate('document.getElementById("fSort").click()');await shot('ranking-menu');await key('Escape');
  assert.equal(await evaluate('document.activeElement.id'),'fSort');assert.equal(await evaluate('document.getElementById("grid").hidden'),false);
  await evaluate('document.getElementById("fSort").click()');await key('Tab');assert.equal(await evaluate('document.activeElement.id'),'gStats');assert.equal(await evaluate('document.getElementById("rankMenu").hidden'),true);
  await evaluate('document.getElementById("moreFilters").click()');assert.equal(await evaluate('document.getElementById("filterDetails").hidden'),false);await shot('filters-open');
  await evaluate('document.getElementById("fFav").click()');assert.equal(await evaluate('document.getElementById("fFav").getAttribute("aria-pressed")'),'true');await evaluate('document.getElementById("fFav").click()');
  // Repo-based sorts pin; palette-based sorts integrate built-ins with ordinary ranks.
  checks.rankings={};
  for(const sort of ['pop','stars','trend','new','updated','complete','walls','legible','colour','unique','name','hue']){
    await nav('#tokyo-night/mock?view=grid&sort='+sort);checks.rankings[sort]=await evaluate('({pinned:!!document.getElementById("builtinsToggle"),hud:document.getElementById("hudRank").textContent})');
    assert.equal(checks.rankings[sort].pinned,['pop','stars','trend','new','updated'].includes(sort));
  }
  await nav('#tokyo-night/mock?view=grid&src=builtin');
  assert.equal(await evaluate('document.querySelectorAll(".card").length'),0);
  await key('Enter');assert.equal(await evaluate('document.getElementById("grid").hidden'),false,'Enter must stay in the grid when all matching cards are collapsed');
  await evaluate('document.getElementById("builtinsToggle").click();document.getElementById("q").focus()');
  await key('Enter');assert.equal(await evaluate('document.getElementById("grid").hidden'),true);
  await nav('#tokyo-night/mock?view=grid');await evaluate('document.getElementById("builtinsToggle").click()');
  await nav('#tokyo-night/mock?view=grid&q=catppuccin');
  assert(await evaluate('!!document.querySelector(".card.pick")'));
  await evaluate('document.getElementById("q").value="catppuccin";document.getElementById("q").dispatchEvent(new InputEvent("input",{bubbles:true}))');
  await key('Enter');assert.equal(await evaluate('document.getElementById("grid").hidden'),true);assert.notEqual(await evaluate('document.getElementById("hudRank").textContent'),'Built into Omarchy');
  await nav('#tokyo-night/mock?view=stats');
  checks.charts=await evaluate('[...document.querySelectorAll(".viz-card")].map(c=>({id:c.id,finding:c.querySelector(".viz-finding")?.textContent,background:getComputedStyle(c).backgroundColor,overflow:getComputedStyle(c).overflow}))');
  assert.equal(checks.charts.length,8);assert(checks.charts.every(c=>c.finding&&c.overflow==='hidden'));
  await shot('stats-top');await shot('hero-numbers','#viz-numbers');await shot('wheel-card','#viz-wheel');await shot('quilt-card','#viz-map');
  checks.quilt=await evaluate('(()=>{const svg=document.querySelector("#viz-map svg"),p=svg._marks.map(m=>m._point);return {count:p.length,minX:Math.min(...p.map(p=>p.x)),maxX:Math.max(...p.map(p=>p.x)),minY:Math.min(...p.map(p=>p.y)),maxY:Math.max(...p.map(p=>p.y))}})()');
  assert.equal(checks.quilt.count,checks.default.total);assert(checks.quilt.minX>0&&checks.quilt.maxX<650&&checks.quilt.minY>0&&checks.quilt.maxY<450);
  async function dragMap(){await evaluate('document.getElementById("viz-map").scrollIntoView({block:"start"})');const points=await evaluate('(()=>{const s=document.querySelector("#viz-map svg"),m=s.getScreenCTM();return [[166.67,350],[216.67,400]].map(([x,y])=>{const p=new DOMPoint(x,y).matrixTransform(m);return {x:p.x,y:p.y}})})()');await call('Input.dispatchMouseEvent',{type:'mousePressed',...points[0],button:'left',buttons:1,clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseMoved',...points[1],button:'left',buttons:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',...points[1],button:'left',clickCount:1});}
  await dragMap();assert(!await evaluate('location.hash.includes("box=")'),'Packed quilt must not turn a visual selection into a different PCA region');assert.equal(await evaluate('document.getElementById("stats").hidden'),false);
  await evaluate('document.querySelector("#viz-map .quilt-toggle").click()');await shot('true-positions','#viz-map');await evaluate('document.querySelector("#viz-map .quilt-toggle").click()');
  for(const chart of checks.charts){await evaluate(`document.querySelector('#${chart.id} .table-toggle').click()`);assert(await evaluate(`!!document.querySelector('#${chart.id} table')`));assert(await evaluate(`!!document.querySelector('#${chart.id} .viz-finding')`));await evaluate(`document.querySelector('#${chart.id} .table-toggle').click()`);}
  const best=await evaluate('GALLERY.themes.reduce((a,t)=>(t.st?.s||0)>(a.st?.s||0)?t:a).slug');
  checks.topStarPopularityRank=await evaluate('GALLERY.themes.filter(t=>!t.builtin).sort((a,b)=>b.r.pop-a.r.pop||a.name.localeCompare(b.name)).findIndex(t=>t.slug==='+JSON.stringify(best)+')+1');assert(checks.topStarPopularityRank>0&&checks.topStarPopularityRank<=3);
  await nav('#'+best+'/mock');await key('i','KeyI');checks.info=await evaluate('document.getElementById("themeInfo").textContent');assert(checks.info.includes('Popularity breakdown')&&checks.info.includes('#'+checks.topStarPopularityRank+' by popularity'));await shot('theme-info');
  checks.names=await evaluate('(()=>{const names=GALLERY.themes.map(t=>t.name);return {generic:names.filter(n=>["Omarchy","Omarchy Theme"].includes(n)),duplicates:names.filter((n,i)=>names.indexOf(n)!==i)}})()');assert.equal(checks.names.generic.length,0);assert.equal(checks.names.duplicates.length,0);
  checks.top10=await evaluate('GALLERY.themes.filter(t=>!t.builtin).sort((a,b)=>b.r.pop-a.r.pop||a.name.localeCompare(b.name)).slice(0,10).map((t,i)=>({rank:i+1,name:t.name,score:t.r.pop,stars:t.st?.s??null}))');
  await nav('#tokyo-night/mock?view=stats');await evaluate('document.querySelector("#viz-map .quilt-toggle").click()');await dragMap();assert(await evaluate('location.hash.includes("box=")'),'True positions must still filter a brushed PCA region');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await nav('#tokyo-night/mock?view=stats');
  checks.mobile=await evaluate('({width:document.documentElement.clientWidth,scrollWidth:document.getElementById("stats").scrollWidth,cards:[...document.querySelectorAll(".viz-card")].map(c=>({width:c.clientWidth,scroll:c.scrollWidth}))})');assert.equal(checks.mobile.scrollWidth,390);assert(checks.mobile.cards.every(c=>c.scroll<=c.width+1));
  await nav('#tokyo-night/mock?view=grid');await evaluate('document.getElementById("moreFilters").click()');
  checks.mobileFilters=await evaluate('({gridWidth:document.getElementById("grid").scrollWidth,cardHeight:document.getElementById("cards").clientHeight,filters:document.getElementById("filterDetails").clientHeight,filterScroll:document.getElementById("filterDetails").scrollHeight})');assert.equal(checks.mobileFilters.gridWidth,390);assert(checks.mobileFilters.cardHeight>150);assert(checks.mobileFilters.filterScroll>checks.mobileFilters.filters);
  checks.consoleErrors=errors;checks.remoteRequests=requests.filter(u=>/^https?:/.test(u));assert.equal(errors.length,0);assert.equal(checks.remoteRequests.length,0);
  fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(checks,null,2)+'\n');
  console.log(`Browser: 1920x1080; ${checks.screenshots.length} screenshots; 12 rankings; menu keys, filters, remembered built-ins, 8 chart tables, compact quilt, info and mobile checks passed.`);
  console.log('Regressions: menu Tab focus, collapsed-grid Enter, quilt drag and True positions brushing passed.');
  console.log('Console errors: 0; remote app requests: 0.');
  for(const t of checks.top10)console.log(`${t.rank}. ${t.name} | ${t.score.toFixed(1)} score | ${t.stars} stars`);
  await call('Browser.close');ws.close();
} finally {ws?.close();browser.kill();fs.closeSync(log);}
