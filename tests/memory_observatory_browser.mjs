/** Optional zero-dependency browser smoke test. Node 22+ and local Chrome.
 * Start the demo server first; never point this at a production service.
 * Output (screenshots, isolated profile and result JSON) goes to OS temp.
 */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, writeFile, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';

let base = process.env.MEMORY_TEST_URL || 'http://127.0.0.1:8766';
const liveReadonly = process.argv.includes('--live-readonly');
const liveActivity = process.argv.includes('--live-activity');
const spawnDemo = process.argv.includes('--spawn-demo');
assert.ok(!(spawnDemo && liveReadonly), 'Synthetic server and live mode are mutually exclusive');
assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
const output = await mkdtemp(path.join(tmpdir(), 'memory-observatory-browser-'));
const profile = path.join(output, 'chrome-profile');
await mkdir(profile);
const executable = process.env.MEMORY_CHROME || (process.platform === 'win32' ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : 'google-chrome');
const chrome = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-extensions', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--window-size=1600,1150', 'about:blank'], {windowsHide:true, stdio:'ignore'});
let socket, ownedServer, sequence=0;
const pending=new Map(), errors=[], failures=[], checks=[];
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn, label, timeout=10000) {const start=Date.now();while(Date.now()-start<timeout){if(await fn())return;await pause(70);}throw new Error(`Timed out: ${label}`);}
function cdp(method,params={}) {return new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method}`));},10000);pending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v);},reject:e=>{clearTimeout(timer);reject(e);}});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression) {const result=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
async function click(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function waitText(selector,text){await until(()=>evaluate(`document.querySelector(${JSON.stringify(selector)})?.textContent.includes(${JSON.stringify(text)})`),`${selector}: ${text}`);}
async function check(name,fn){try{await fn();checks.push(name);console.log(`PASS ${name}`);}catch(error){failures.push({name,message:error.message});console.log(`FAIL ${name}: ${error.message}`);}}
async function screenshot(name,full=false){let clip;if(full){const metrics=await cdp('Page.getLayoutMetrics');clip={x:0,y:0,width:metrics.cssContentSize.width,height:metrics.cssContentSize.height,scale:1};}const result=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:full,...(clip?{clip}:{})});await writeFile(path.join(output,name),Buffer.from(result.data,'base64'));}

try {
  if(spawnDemo){
    let startup='';
    ownedServer=spawn(process.env.MEMORY_PYTHON||'python',['-B','-m','deploy.memory_observatory.server','--port','0'],{windowsHide:true,stdio:['ignore','pipe','pipe']});
    ownedServer.stdout.on('data',chunk=>startup+=chunk.toString());
    await until(()=>{const match=startup.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){base=match[0];return true;}return false;},'owned synthetic server');
  }
  let devtools;
  await until(async()=>{try{devtools=await readFile(path.join(profile,'DevToolsActivePort'),'utf8');return true;}catch{return false;}},'Chrome startup');
  const port=devtools.split('\n')[0];
  const pages=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  socket.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.id){const item=pending.get(m.id);if(item){pending.delete(m.id);if(m.error)item.reject(new Error(m.error.message));else item.resolve(m.result);}}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);});
  await cdp('Page.enable');await cdp('Runtime.enable');
  await cdp('Emulation.setDeviceMetricsOverride',{width:1600,height:1150,deviceScaleFactor:1,mobile:false});
  await cdp('Page.navigate',{url:base});
  if(liveReadonly){
    // Never import/reset, export private text, or capture private screenshots.
    await waitText('#dataset-badge','真实只读');
    await check('real source is labelled read-only with a nonempty scoped view',async()=>{
      assert.equal(await evaluate('document.querySelector("#source-panel").hidden'),false);
      assert.ok(await evaluate('document.querySelectorAll(".memory-card").length > 0'));
      assert.equal(await evaluate('document.querySelector("#import-button").hidden && document.querySelector("#reset-dataset").hidden'),true);
    });
    await check('upstream evidence is separated from simulation controls',async()=>{
      assert.equal(await evaluate('Boolean(document.querySelector(".upstream-facts"))'),true);
      assert.equal(await evaluate('document.querySelector("#footer-mode").textContent.includes("本地模拟")'),true);
      assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    });
    await check('viewing and local projection keep the last sync time unchanged',async()=>{
      const before=await evaluate('fetch("/api/snapshot").then(r=>r.json()).then(d=>d.connection.synced_at)');
      await click('.memory-card');await click('#dimension');
      await evaluate('document.querySelector("#days").value=30;document.querySelector("#days").dispatchEvent(new Event("input",{bubbles:true}))');
      await pause(250);
      assert.equal(await evaluate('fetch("/api/snapshot").then(r=>r.json()).then(d=>d.connection.synced_at)'),before);
      await click('#reset-simulation');
    });
    await check('manual synchronization succeeds without changing source mode',async()=>{
      const before=await evaluate('fetch("/api/snapshot").then(r=>r.json()).then(d=>d.connection.synced_at)');
      await click('#sync-livingmemory');
      await until(()=>evaluate('!document.querySelector("#sync-livingmemory").disabled'),'source refresh',45000);
      assert.ok(await evaluate(`fetch("/api/snapshot").then(r=>r.json()).then(d=>d.connection.state === "ready" && d.connection.synced_at !== ${JSON.stringify(before)})`));
    });
    await check('real source mobile layout has no horizontal overflow',async()=>{
      await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
      await pause(200);assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    });
    if(liveActivity)await check('real activity room stays read-only and loads the approved character',async()=>{
      await cdp('Page.navigate',{url:base+'/room'});
      await until(()=>evaluate('document.querySelector("#room-source")?.textContent !== "正在读取来源"'),'activity source loaded');
      // Cached activity deliberately becomes stale after three minutes; refresh
      // explicitly instead of mistaking stale evidence for a source failure.
      await click('#room-refresh');
      await until(()=>evaluate('!document.querySelector("#room-refresh").disabled'),'activity read-only refresh',45000);
      const metadata=await evaluate('fetch("/api/room").then(r=>r.json()).then(s=>({mode:s.mode,connection:s.connection}))');
      assert.deepEqual(metadata,{mode:'activity',connection:'ready'});
      await waitText('#room-source','真实活动账本');
      await until(()=>evaluate('document.querySelector("#room-character").dataset.assetReady === "true"'),'real room character decoded');
      assert.equal(await evaluate('document.querySelector("button[data-zone=overview]").getAttribute("aria-pressed")'),'true');
      assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
      assert.equal(await evaluate('fetch("/api/room").then(r=>r.json()).then(s=>s.mode==="activity" && s.connection==="ready")'),true);
    });
    await check('real source view has no uncaught browser JavaScript exceptions',async()=>assert.deepEqual(errors,[]));
  } else {
  await waitText('#count-total','32');
  await click('#motion'); // Static for consistent screenshot, no data effects.
  await screenshot('desktop.png',true);
  await check('desktop loads 32 scoped memories with no horizontal overflow',async()=>{
    assert.equal(await evaluate('document.querySelectorAll(".memory-card").length'),32);
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    assert.equal(await evaluate('document.querySelector("#dataset-badge").textContent'),'演示记忆');
    assert.equal(await evaluate('document.querySelector(".gist-box").textContent.includes("有些陪伴不需要很多话")'),true);
  });
  await check('memory selection and 2D controls update the actual view',async()=>{
    await click('.memory-card');
    assert.equal(await evaluate('document.querySelector(".detail-title").textContent'),'第一次认真说你好');
    await click('#dimension');assert.equal(await evaluate('document.querySelector("#dimension").textContent'),'2D 阅读');
    await click('#dimension');await click('#zoom-in');await click('#recenter');
    await evaluate('[...document.querySelectorAll(".memory-card")].find(x=>x.querySelector("h3").textContent === "雨声里的晚安").click()');
    assert.equal(await evaluate('document.querySelector(".detail-title").textContent'),'雨声里的晚安');
    assert.ok(Number(await evaluate('document.querySelector("#constellation").dataset.relatedNodes'))>0);
  });
  await check('star map zooms in far enough',async()=>{
    const result=await evaluate(`(()=>{const g=document.querySelector('#constellation').__constellation;g.twoD=false;g.camera.distance=2.35;for(let i=0;i<30;i++)g.zoomBy(1.2);const d=g.camera.distance;g.twoD=true;g.flat={scale:1,panX:0,panY:0};for(let i=0;i<30;i++)g.zoomBy(1.2);const s=g.flat.scale;g.twoD=false;g.flat={scale:1,panX:0,panY:0};g.resetCamera(true);return {d,s};})()`);
    assert.ok(result.d<=.66);assert.ok(result.s>=5.9);
  });
  await check('starfields separate types and keep positions stable',async()=>{
    const result=await evaluate(`(async()=>{const {layoutStarfields}=await import('/graph.js');const d=await fetch('/api/snapshot').then(r=>r.json());const a=layoutStarfields(d.memories),b=layoutStarfields(d.memories);const extra=[...d.memories,{...d.memories[0],id:'extra-test'}];const c=layoutStarfields(extra);return {groups:a.groups.filter(g=>g.count>0).length,stable:a.nodes.every((n,i)=>n.x===b.nodes[i].x&&n.y===b.nodes[i].y),local:a.nodes.every(n=>{const m=c.nodes.find(x=>x.id===n.id);return m&&m.x===n.x&&m.y===n.y;}),inside:a.nodes.every(n=>{const g=a.groups.find(g=>g.kind===n.kind);return Math.hypot(n.x-g.center[0],n.z-g.center[2])<=g.radius*1.3+1e-6;})};})()`);
    assert.ok(result.groups>=1);
    assert.equal(result.stable,true);
    assert.equal(result.local,true);
    assert.equal(result.inside,true);
    assert.ok(Number(await evaluate('document.querySelector("#constellation").dataset.highlightedEdges'))>0);
    assert.equal(await evaluate('document.querySelector("#selection-summary").textContent.includes("直接关联")'),true);
  });
  await check('clicking the selected node again clears the connection',async()=>{
    const spot=await evaluate(`(()=>{const c=document.querySelector('#constellation'),g=c.__constellation;const p=g.positions.find(p=>p.id===g.selected);if(!p)return null;const r=c.getBoundingClientRect();return {x:r.left+p.x,y:r.top+p.y};})()`);
    assert.ok(spot);
    await evaluate('document.querySelector("#constellation").__constellation.categoryHits=[]');
    await evaluate(`(()=>{const c=document.querySelector('#constellation');const o={bubbles:true,pointerId:7,pointerType:'mouse',clientX:${spot.x},clientY:${spot.y}};c.dispatchEvent(new PointerEvent('pointerdown',o));c.dispatchEvent(new PointerEvent('pointerup',o));return true;})()`);
    await until(()=>evaluate('document.querySelector("#constellation").__constellation.selected === null'),'selection cleared');
    assert.equal(await evaluate('document.querySelector("#memory-detail .empty-state") !== null'),true);
    assert.equal(await evaluate('document.querySelector("#constellation").dataset.relatedNodes'),'0');
    await evaluate('[...document.querySelectorAll(".memory-card")].find(x=>x.querySelector("h3").textContent === "雨声里的晚安").click()');
  });
  await check('camera survives a data refresh',async()=>{
    await until(()=>evaluate('document.querySelector("#constellation").__constellation.cameraTween === null'),'camera settles');
    const before=await evaluate('(()=>{const g=document.querySelector("#constellation").__constellation;return {yaw:g.camera.yaw,pitch:g.camera.pitch,distance:g.camera.distance}})()');
    await evaluate('(()=>{const g=document.querySelector("#constellation").__constellation;g.setData(g.data,g.selected,g.visibleKinds);return true;})()');
    const after=await evaluate('(()=>{const g=document.querySelector("#constellation").__constellation;return {yaw:g.camera.yaw,pitch:g.camera.pitch,distance:g.camera.distance}})()');
    assert.deepEqual(after,before);
  });
  await check('clicking a starfield label focuses it',async()=>{
    const info=await evaluate('(()=>{const g=document.querySelector("#constellation").__constellation;const b=g.categoryHits[0];return b?{kind:b.kind,x:b.x,y:b.y,w:b.w,h:b.h}:null;})()');
    assert.ok(info);
    await evaluate(`(()=>{const c=document.querySelector('#constellation');const r=c.getBoundingClientRect();const o={bubbles:true,pointerId:8,pointerType:'mouse',clientX:r.left+${info.x+info.w/2},clientY:r.top+${info.y+info.h/2}};c.dispatchEvent(new PointerEvent('pointerdown',o));c.dispatchEvent(new PointerEvent('pointerup',o));return true;})()`);
    await until(()=>evaluate(`document.querySelector('#constellation').__constellation.focusedKind === ${JSON.stringify(info.kind)}`),'starfield focused');
    await until(()=>evaluate('document.querySelector("#constellation").__constellation.cameraTween === null'),'camera focus settles');
    const target=await evaluate(`(()=>{const g=document.querySelector('#constellation').__constellation;const grp=g.groups.find(x=>x.kind===${JSON.stringify(info.kind)});return {tx:g.camera.target[0],ty:g.camera.target[1],tz:g.camera.target[2],gx:grp.center[0],gy:grp.center[1],gz:grp.center[2]};})()`);
    assert.ok(Math.hypot(target.tx-target.gx,target.ty-target.gy,target.tz-target.gz)<.02);
    await evaluate('document.querySelector("#constellation").__constellation.recenter()');
  });
  await check('future simulation fades detail but preserves source confidence',async()=>{
    const before=await evaluate('[...document.querySelectorAll(".detail-score strong")].map(x=>x.textContent)');
    const immediate=await evaluate(`(()=>{const g=document.querySelector('#constellation').__constellation,s=document.querySelector('#days');const n=g.nodes.find(n=>!n.protected);const before=n.detail;s.value='180';s.dispatchEvent(new Event('input',{bubbles:true}));return {before,after:n.detail};})()`);
    assert.ok(immediate.after < immediate.before);
    await until(()=>evaluate('document.querySelector("#constellation").dataset.transitioning === "true"'),'continuous fade begins');
    await evaluate('window.__fadeBefore=document.querySelector("#constellation").toDataURL();void 0');await pause(90);
    assert.equal(await evaluate('window.__fadeBefore !== document.querySelector("#constellation").toDataURL()'),true);
    await until(()=>evaluate('document.querySelector("#constellation").dataset.transitioning === "false"'),'fade settles');
    await waitText('#snapshot-date','2027/04/26');
    const after=await evaluate('[...document.querySelectorAll(".detail-score strong")].map(x=>x.textContent)');
    assert.ok(Number(after[0])<Number(before[0]));assert.equal(after[1],before[1]);
  });
  await check('simulated pin and reset are reversible',async()=>{
    await click('.detail-actions button:nth-child(2)');
    await until(()=>evaluate('document.querySelector(".detail-score strong").textContent === "100"'),'pin');
    await click('#reset-simulation');await waitText('#day-label','快照当日');
    await until(()=>evaluate('document.querySelector(".detail-actions button:nth-child(2)").getAttribute("aria-pressed") === "false"'),'reset');
  });
  await check('cue retrieval explains seed and one-hop associations',async()=>{
    await evaluate('document.querySelector("#query").value="雨声";document.querySelector("#search-form").requestSubmit()');
    await until(()=>evaluate('document.querySelectorAll(".trace-item").length > 1'),'recall trace');
    assert.equal(await evaluate('document.querySelector("#trace-list").textContent.includes("文本/主题线索匹配")'),true);
    await screenshot('recall.png',true);
  });
  await check('timeline and lab navigation work',async()=>{
    await click('[data-view="timeline"]');
    assert.equal(await evaluate('document.querySelector("#timeline-view").hidden'),false);
    assert.equal(await evaluate('document.querySelectorAll(".timeline-item").length'),32);
    await click('[data-view="lab"]');assert.equal(await evaluate('document.querySelector("#lab-view").hidden'),false);
    await click('[data-view="atlas"]');
  });
  await check('group scope never exposes private matches or cards',async()=>{
    await evaluate('const s=document.querySelector("#scope");s.value="演示 · 群聊隔离";s.dispatchEvent(new Event("change",{bubbles:true}))');
    await waitText('#count-total','03');assert.equal(await evaluate('document.querySelectorAll(".memory-card").length'),3);
    assert.equal(await evaluate('document.querySelectorAll(".trace-item").length'),0);
    assert.equal(await evaluate('document.querySelector("#memory-detail").textContent.includes("雨声里的晚安")'),false);
  });
  await check('JSON upload renders malicious-looking content literally',async()=>{
    const fixture={schema:'memory-observatory/v1',as_of:'2026-10-28T12:00:00Z',label:'Test import',memories:[{id:'unsafe',scope:'test',title:'<img src=x onerror="window.INJECTED=1">',text:'<script>window.INJECTED=1</script>',created_at:'2026-10-28T12:00:00Z',source:'Synthetic browser test'}]};
    const filename=path.join(output,'fixture.json');await writeFile(filename,JSON.stringify(fixture));
    await click('#import-button');
    const documentNode=await cdp('DOM.getDocument');const input=await cdp('DOM.querySelector',{nodeId:documentNode.root.nodeId,selector:'#import-file'});
    await cdp('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[filename]});
    await waitText('#dataset-badge','导入快照');
    assert.equal(await evaluate('window.INJECTED || 0'),0);
    assert.equal(await evaluate('document.querySelector(".detail-title").textContent'),fixture.memories[0].title);
    assert.equal(await evaluate('document.querySelectorAll("#memory-detail script, #memory-detail img").length'),0);
    assert.equal(await evaluate('document.querySelector("#info-dialog").open'),false);
  });
  await click('#reset-dataset');await waitText('#count-total','32');
  await check('mobile layout fits 390px with usable controls',async()=>{
    await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await pause(200);assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    await until(()=>evaluate('document.querySelector("#toast").hidden'),'toast dismissed');
    await screenshot('mobile.png',true);
    await click('#about-button');assert.equal(await evaluate('document.querySelector("#info-dialog").open'),true);
    await click('#info-dialog .dialog-close');
  });
  await check('room opens from the atlas and labels fiction independently',async()=>{
    await cdp('Emulation.setDeviceMetricsOverride',{width:1600,height:1150,deviceScaleFactor:1,mobile:false});
    await click('a[href="/room"]');await waitText('#room-source','虚构演示');
    assert.equal(await evaluate('document.querySelector("button[data-zone=overview]").getAttribute("aria-pressed")'),'true');
    await until(()=>evaluate('document.querySelector("#room-character").dataset.assetReady === "true"'),'character image decoded');
    assert.equal(await evaluate('document.querySelector("#journal-content").textContent.includes("不等于目标完成")'),true);
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    await click('#room-motion');await screenshot('room-desktop.png',true);
  });
  await check('room corners show actual snapshot records and keep controls read-only',async()=>{
    await click('[data-zone="shelf"]');await waitText('#journal-content','深海来信');
    await click('[data-zone="window"]');assert.equal(await evaluate('document.querySelectorAll(".journal-item").length'),3);
    await click('[data-zone="rest"]');await waitText('#journal-content','不模拟体力');
    assert.equal(await evaluate('document.querySelector("#room-refresh").disabled'),true);
    await click('[data-zone="desk"]');assert.equal(await evaluate('document.querySelectorAll("[data-zone][aria-pressed=true]").length'),1);
    assert.equal(await evaluate('document.querySelector("#room-character").dataset.action'),'desk');
  });
  await check('project shelf opens books and stages without changing source records',async()=>{
    const before=await evaluate('fetch("/api/room").then(r=>r.json())');
    await click('[data-zone="shelf"]');
    assert.equal(await evaluate('document.querySelector("#project-library").hidden'),false);
    await waitText('#project-library','收集海里的光');
    assert.equal(await evaluate('document.querySelectorAll(".project-book").length'),2);
    await click('.project-stage summary');
    await waitText('.review-leaf','还缺少独立资料和反例');
    assert.equal(await evaluate('document.querySelector(".work-leaf").textContent.includes("第 2 版")'),true);
    assert.equal(await evaluate('document.querySelector("#project-source-note").textContent.includes("虚构演示")'),true);
    await screenshot('room-projects-desktop.png',true);
    await evaluate('document.querySelector("[data-project=demo-project-rain]").focus()');
    await click('[data-project="demo-project-rain"]');
    await waitText('#project-reading','这个项目已经收尾');
    assert.equal(await evaluate('document.activeElement.dataset.project'),'demo-project-rain');
    await evaluate('document.querySelector(".project-stage summary").focus()');
    await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',text:'\r',unmodifiedText:'\r',windowsVirtualKeyCode:13});
    await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    assert.equal(await evaluate('document.querySelector(".project-stage").open'),false,'Enter must collapse the stage');
    await click('[data-zone="desk"]');assert.equal(await evaluate('document.querySelector("#project-library").hidden'),true);
    await click('[data-zone="shelf"]');
    assert.equal(await evaluate('document.querySelector(".project-book[aria-pressed=true]").dataset.project'),'demo-project-rain');
    assert.equal(await evaluate('document.querySelector(".project-stage").open'),false,'Changing room zones must preserve the closed stage');
    assert.deepEqual(await evaluate('fetch("/api/room").then(r=>r.json())'),before);
  });
  await check('project refresh keeps the selected book and expansion; legacy and unsupported are explicit',async()=>{
    const original=await evaluate('fetch("/api/room").then(r=>r.json())');
    let data={...original,mode:'activity',connection:'ready'},refreshes=0;
    await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/room*',requestStage:'Request'}]});
    const handler=async event=>{const msg=JSON.parse(event.data);if(msg.method==='Fetch.requestPaused'){if(msg.params.request.url.endsWith('/refresh'))refreshes++;await cdp('Fetch.fulfillRequest',{requestId:msg.params.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(data)).toString('base64')});}};
    socket.addEventListener('message',handler);
    try{
      await cdp('Page.reload');await waitText('#room-source','真实活动账本');await click('#room-motion');await click('[data-zone="shelf"]');
      await click('[data-project="demo-project-rain"]');await click('.project-stage summary');await pause(80);
      const camera=await evaluate('document.querySelector("#room-character").dataset.action');
      data={...data,projects:{...data.projects,enabled:false}};
      await click('#room-refresh');await waitText('.project-disabled','目前关闭');
      assert.equal(refreshes,1);
      assert.equal(await evaluate('document.querySelector(".project-book[aria-pressed=true]").dataset.project'),'demo-project-rain');
      assert.equal(await evaluate('document.querySelector(".project-stage").open'),false);
      assert.equal(await evaluate('document.querySelector("#room-character").dataset.action'),camera);
      data={...data,projects:{state:'unsupported',items:[],enabled:false,total:0}};
      await click('#room-refresh');await waitText('#project-books','暂不兼容');
      assert.equal(await evaluate('document.querySelectorAll(".project-book").length'),0);
      delete data.projects;await click('#room-refresh');await waitText('#project-books','还没有项目册');
      assert.equal(await evaluate('document.querySelector("#journal-content").textContent.includes("深海来信")'),true);
    }finally{await cdp('Fetch.disable');socket.removeEventListener('message',handler);await cdp('Page.reload');await waitText('#room-source','虚构演示');await click('#room-motion');}
  });
  // Exercise the real room module with synthetic HTTP receipts and a controllable
  // monotonic clock; no source, model, bot, or real database is contacted.
  async function withRoomSync(run,initial={}){
    const original=await(await fetch(base+'/api/room')).json();
    const state={data:{...original,mode:'activity',connection:'ready',age_seconds:0,...initial},posts:0,fail:false,requests:[]};
    const injection=await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`
      const interval=window.setInterval,timeout=window.setTimeout,now=performance.now.bind(performance);window.__roomElapsed=0;window.__roomClock=null;
      performance.now=()=>window.__roomClock??now()+window.__roomElapsed;
      window.setInterval=function(fn,ms,...args){if(ms===60000)window.__roomSyncTick=fn;return interval(fn,ms,...args);};
      window.setTimeout=function(fn,ms,...args){if(fn.name==='autoTick'){window.__roomSyncTick=fn;window.__roomSyncDelay=ms;}return timeout(fn,ms,...args);};
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>document.visibilityState==='hidden'});
    `});
    await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/*',requestStage:'Request'}]});
    const handler=async event=>{
      const msg=JSON.parse(event.data);if(msg.method!=='Fetch.requestPaused')return;
      const p=msg.params,url=new URL(p.request.url).pathname;state.requests.push([p.request.method,url,p.request.postData]);
      const refresh=url==='/api/room/refresh',allowed=(url==='/api/room'&&p.request.method==='GET')||(refresh&&p.request.method==='POST'&&p.request.postData==='{}');
      if(refresh){state.posts++;await pause(160);}
      const failed=refresh&&state.fail;
      await cdp('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:!allowed?403:failed?503:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(!allowed||failed?{error:'synthetic unavailable'}:state.data)).toString('base64')});
    };
    socket.addEventListener('message',handler);
    const tick=async()=>{await evaluate('window.__roomElapsed+=60001;window.__roomSyncTick?.();void 0');};
    const settled=async()=>until(()=>evaluate('document.querySelector("#room-refresh").textContent.includes("同步近况")'),'room refresh settled');
    try{
      await evaluate('localStorage.removeItem("observatory-room-auto-sync")');
      await cdp('Page.reload');await until(()=>evaluate('document.querySelector("#room-source")?.textContent !== "正在读取来源"'),'room source loaded');
      await click('#room-motion');await run(state,tick,settled);
      assert.ok(state.requests.every(([method,url,body])=>(method==='GET'&&url==='/api/room')||(method==='POST'&&url==='/api/room/refresh'&&body==='{}')),'Only the existing read-only room boundary may be used');
    }finally{
      await cdp('Fetch.disable');socket.removeEventListener('message',handler);
      await cdp('Page.removeScriptToEvaluateOnNewDocument',{identifier:injection.identifier});
      await evaluate('localStorage.removeItem("observatory-room-auto-sync")');
      await cdp('Page.reload');await waitText('#room-source','虚构演示');await click('#room-motion');
    }
  }
  await check('room auto sync preserves book, chapter, focus, reading anchor and camera',async()=>{
    await withRoomSync(async(state,tick,settled)=>{
      assert.equal(state.posts,0,'Loading the cached view does not read upstream again');
      assert.equal(await evaluate('typeof window.__roomSyncTick'),'function','Configured sources must schedule their next read');
      await click('[data-zone="shelf"]');await click('.project-book[data-project="demo-project-rain"]');await click('.project-stage summary');
      const before=await evaluate(`(()=>{const s=document.querySelector('.project-stage summary');s.focus({preventScroll:true});window.scrollTo({top:scrollY+s.getBoundingClientRect().top-130,behavior:'instant'});return {top:s.getBoundingClientRect().top,key:s.parentElement.dataset.stageKey,camera:document.querySelector('#room-camera').getAttribute('transform'),action:document.querySelector('#room-character').dataset.action};})()`);
      state.data.projects.enabled=false;await tick();await settled();await waitText('.project-disabled','目前关闭');
      const after=await evaluate(`({top:document.querySelector('.project-stage summary').getBoundingClientRect().top,key:document.activeElement.parentElement.dataset.stageKey,camera:document.querySelector('#room-camera').getAttribute('transform'),action:document.querySelector('#room-character').dataset.action,book:document.querySelector('.project-book[aria-pressed=true]').dataset.project,open:document.querySelector('.project-stage').open})`);
      assert.ok(Math.abs(after.top-before.top)<2,'The visible chapter must stay at its reading offset');
      assert.equal(after.key,before.key);assert.equal(after.camera,before.camera);assert.equal(after.action,before.action);
      assert.equal(after.book,'demo-project-rain');assert.equal(after.open,false);
      // Same content with a new receipt keeps actual DOM and native selection.
      await evaluate(`window.__roomStage=document.querySelector('.project-stage');const range=document.createRange();range.selectNodeContents(document.querySelector('.project-intro h3'));getSelection().removeAllRanges();getSelection().addRange(range);window.__roomSelectedText=getSelection().toString();`);
      state.data.as_of+=60;await tick();await settled();
      assert.equal(await evaluate('window.__roomStage === document.querySelector(".project-stage") && getSelection().toString() === window.__roomSelectedText'),true);
      // The user can navigate while a receipt is in flight; its old zone cannot win.
      await tick();await click('[data-zone="window"]');await settled();
      assert.equal(await evaluate('document.querySelector("button[data-zone=window]").getAttribute("aria-pressed")'),'true');
      assert.equal(await evaluate('document.querySelector("#room-character").dataset.action'),'window');
      assert.equal(state.posts,3);
      await screenshot('room-sync-desktop.png',true);
    });
  });
  await check('room auto sync pauses hidden pages, coalesces reads and persists manual mode',async()=>{
    await withRoomSync(async(state,tick,settled)=>{
      await evaluate('Object.defineProperty(document,"visibilityState",{configurable:true,get:()=>"hidden"});document.dispatchEvent(new Event("visibilitychange"))');
      await tick();await pause(200);assert.equal(state.posts,0);await waitText('#room-sync-state','暂停');
      await evaluate('delete document.visibilityState;document.dispatchEvent(new Event("visibilitychange"));window.__roomSyncTick();document.querySelector("#room-refresh").click();void 0');
      await settled();assert.equal(state.posts,1,'Return to the page, timer, and manual click share one in-flight read');
      await evaluate('document.dispatchEvent(new Event("visibilitychange"));window.__roomSyncTick();void 0');await pause(200);assert.equal(state.posts,1,'Rapid tab changes must not start extra reads');
      await click('#room-auto-sync');await tick();await pause(200);assert.equal(state.posts,1);
      await cdp('Page.reload');await waitText('#room-sync-state','手动同步');
      assert.equal(await evaluate('document.querySelector("#room-auto-sync").checked'),false);
      await tick();await pause(200);assert.equal(state.posts,1);
      await click('#room-refresh');await settled();assert.equal(state.posts,2,'Manual refresh remains available when auto sync is off');
      await click('#room-auto-sync');await tick();await settled();assert.equal(state.posts,3);
      await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
      assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
      await screenshot('room-sync-mobile.png',true);
      await cdp('Emulation.setDeviceMetricsOverride',{width:1600,height:1150,deviceScaleFactor:1,mobile:false});
    });
  });
  await check('room sync failure keeps old records and receipts, then recovers without fiction',async()=>{
    await withRoomSync(async(state,tick,settled)=>{
      await click('[data-zone="shelf"]');await click('.project-book[data-project="demo-project-rain"]');
      const before=await evaluate('({title:document.querySelector(".project-intro h3").textContent,stamp:document.querySelector("#room-synced").textContent})');
      state.fail=true;await tick();await settled();await waitText('#room-source','旧快照');await waitText('#project-source-note','只供回看');
      assert.deepEqual(await evaluate('({title:document.querySelector(".project-intro h3").textContent,stamp:document.querySelector("#room-synced").textContent})'),before);
      assert.equal(await evaluate('document.querySelector("#room-toast").hidden'),true,'Automatic failures must not repeatedly toast over the reader');
      state.fail=false;state.data.projects.items.find(p=>p.id==='demo-project-rain').title='雨声笔记 · 新的摘页';await tick();await settled();
      await waitText('#room-source','真实活动');await waitText('.project-intro h3','新的摘页');
      // A configured but initially unavailable source may recover automatically.
      const ready=state.data;state.data={...ready,mode:'unavailable',connection:'unavailable'};
      await cdp('Page.reload');await waitText('#room-source','未就绪');
      state.data=ready;await tick();await settled();await waitText('#room-source','真实活动');
    });
  });
  await check('demo and unconfigured rooms never poll a source',async()=>{
    for(const initial of [{mode:'demo',connection:'demo'},{mode:'unavailable',connection:'unconfigured'}]){
      await withRoomSync(async(state,tick)=>{
        assert.equal(await evaluate('document.querySelector("#room-auto-sync").disabled'),true);
        await tick();await evaluate('document.dispatchEvent(new Event("visibilitychange"));document.querySelector("#room-refresh").click();void 0');
        await pause(200);assert.equal(state.posts,0);
      },initial);
    }
  });
  await check('room sync keeps the visible journal record when new records arrive or reads fail',async()=>{
    await withRoomSync(async(state,tick,settled)=>{
      const template=state.data.decisions[0];
      state.data.decisions=Array.from({length:10},(_,i)=>({...template,id:`test-decision-${i}`,reason:`第 ${i} 条离线安排。`+'这是供滚动阅读验收使用的虚构记录。'.repeat(8),at:template.at-i*100}));
      await click('#room-refresh');await settled();await click('[data-zone="window"]');
      const before=await evaluate(`(()=>{const list=document.querySelector('.record-list');list.scrollTop=640;const edge=list.getBoundingClientRect().top,row=[...list.children].find(n=>n.getBoundingClientRect().bottom>edge);return {text:row.querySelector('.entry-text').textContent,offset:row.getBoundingClientRect().top-edge,scroll:list.scrollTop};})()`);
      assert.ok(before.scroll>0,'Fixture must exercise the inner scroll container');
      state.data.decisions.unshift({...template,id:'test-new-decision',reason:'刚刚添加的离线安排。',at:template.at+100});
      await tick();await settled();
      const reading=()=>evaluate(`(()=>{const list=document.querySelector('.record-list'),row=[...list.children].find(n=>n.querySelector('.entry-text').textContent===${JSON.stringify(before.text)});return {offset:row.getBoundingClientRect().top-list.getBoundingClientRect().top,scroll:list.scrollTop};})()`);
      const after=await reading();assert.ok(Math.abs(after.offset-before.offset)<2,'New records must not displace the journal entry being read');
      assert.ok(after.scroll>before.scroll,'The list must account for the new entry inserted above');
      state.fail=true;await tick();await settled();await waitText('#room-source','旧快照');
      assert.ok(Math.abs((await reading()).offset-before.offset)<2,'A stale warning must not reset inner reading position');
    });
  });
  await check('room mobile journal keeps its page reading position when a new entry arrives',async()=>{
    try{
      await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
      await withRoomSync(async(state,tick,settled)=>{
        const template=state.data.decisions[0];
        state.data.decisions=Array.from({length:10},(_,i)=>({...template,id:`mobile-decision-${i}`,reason:`第 ${i} 页虚构安排。`+'这段离线文字仅用于检查手机阅读位置。'.repeat(6)}));
        await click('#room-refresh');await settled();await click('[data-zone="window"]');
        const before=await evaluate(`(()=>{const list=document.querySelector('.record-list'),row=list.children[3];window.scrollTo({top:scrollY+row.getBoundingClientRect().top-140,behavior:'instant'});return {text:row.querySelector('.entry-text').textContent,top:row.getBoundingClientRect().top,inner:list.scrollHeight-list.clientHeight};})()`);
        assert.equal(before.inner,0,'The mobile list must expand into the page');
        state.data.decisions.unshift({...template,id:'mobile-new',reason:'新到的一条离线记录。'});await tick();await settled();
        const after=await evaluate(`[...document.querySelectorAll('.record-list .journal-item')].find(n=>n.querySelector('.entry-text').textContent===${JSON.stringify(before.text)}).getBoundingClientRect().top`);
        assert.ok(Math.abs(after-before.top)<2,'Mobile refresh must keep the entry at its page reading offset');
        assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
      });
    }finally{await cdp('Emulation.setDeviceMetricsOverride',{width:1600,height:1150,deviceScaleFactor:1,mobile:false});}
  });
  await check('room sync schedules the remaining delay instead of missing a cycle after timer jitter',async()=>{
    await withRoomSync(async(state,_tick,settled)=>{
      await evaluate('window.__roomClock=100000;window.__roomSyncTick();void 0');await settled();assert.equal(state.posts,1);
      await evaluate('window.__roomClock=159997;window.__roomSyncTick();void 0');
      assert.equal(state.posts,1,'An early timer must not read before the interval');
      assert.equal(await evaluate('window.__roomSyncDelay'),3,'An early tick must reschedule the remaining 3ms, not skip a whole minute');
      await evaluate('window.__roomClock=160000;window.__roomSyncTick();void 0');await settled();assert.equal(state.posts,2);
    });
  });
  await check('room shows rhythm as a dated decision without starting activity',async()=>{
    const before=await evaluate('fetch("/api/room").then(r=>r.json())');
    await click('[data-zone="overview"]');
    assert.ok(await evaluate('document.querySelector(".rhythm-note").textContent.includes("选择当时")'));
    await click('[data-zone="rest"]');
    assert.ok(await evaluate('document.querySelector(".rhythm-note").textContent.includes("12 分钟活动")'));
    const after=await evaluate('fetch("/api/room").then(r=>r.json())');
    assert.deepEqual(after,before);
    await click('[data-zone="desk"]');
  });
  await check('camera and character move continuously and rapid navigation ends at latest zone',async()=>{
    await click('#room-motion');
    const before=await evaluate('document.querySelector("#room-camera").getAttribute("transform")');
    await click('[data-zone="shelf"]');await pause(180);
    const during=await evaluate('document.querySelector("#room-camera").getAttribute("transform")');
    assert.notEqual(before,during);
    assert.equal(await evaluate('document.querySelector("#room-character").dataset.action'),'walk');
    await click('[data-zone="window"]');await pause(180);await click('[data-zone="rest"]');
    await until(()=>evaluate('document.querySelector("#room-character").dataset.action === "rest"'),'latest navigation completes');
    await pause(300);assert.equal(await evaluate('document.querySelector("#room-character").dataset.zone'),'rest');
    await screenshot('room-rest.png',true);
    await click('[data-zone="desk"]');await until(()=>evaluate('document.querySelector("#room-character").dataset.action === "desk"'),'writing pose');
    await screenshot('room-desk.png',true);
    await click('[data-zone="desk"]');await pause(30);
    assert.equal(await evaluate('document.querySelector("#room-character").dataset.action'),'desk');
    const frame=await evaluate('document.querySelector("#room-character").dataset.frame');await pause(250);
    assert.notEqual(await evaluate('document.querySelector("#room-character").dataset.frame'),frame);
  });
  await check('window uses Shanghai real time and supports day / dusk / night',async()=>{
    const result=await evaluate(`import('/room-scene.js').then(m=>['2026-10-30T22:00:00Z','2026-10-30T02:00:00Z','2026-10-30T10:00:00Z','2026-10-30T14:00:00Z'].map(t=>m.dayPhase(new Date(t)).phase))`);
    assert.deepEqual(result,['dawn','day','sunset','night']);
    await evaluate(`import('/room-scene.js').then(m=>m.RoomScene.prototype.clock(new Date('2026-10-30T02:00:00Z')))`);
    assert.equal(await evaluate('getComputedStyle(document.querySelector("#sky-moon")).display'),'none');
    await click('[data-zone="window"]');await until(()=>evaluate('document.querySelector("#room-character").dataset.action === "window"'),'window pose');await screenshot('room-window-day.png',true);
    await evaluate(`import('/room-scene.js').then(m=>m.RoomScene.prototype.clock(new Date('2026-10-30T14:00:00Z')))`);
    assert.equal(await evaluate('getComputedStyle(document.querySelector("#sky-sun")).display'),'none');
    await screenshot('room-window-night.png',true);
  });
  await check('window animation renders every one of its eight frames',async()=>{
    const result=await evaluate(`(async()=>{const canvas=document.querySelector('#room-character'),ctx=canvas.getContext('2d'),seen=new Map(),end=performance.now()+7000;
      while(performance.now()<end&&seen.size<8){const frame=Number(canvas.dataset.frame);if(!seen.has(frame)){const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;let opaque=0,hash=2166136261,bottom=0;for(let i=3;i<pixels.length;i+=4){if(pixels[i]>128){opaque++;bottom=Math.floor(i/4/canvas.width);}hash=Math.imul(hash^pixels[i],16777619);}seen.set(frame,{opaque,hash,bottom,ready:canvas.dataset.assetReady});}await new Promise(r=>setTimeout(r,80));}
      return [...seen].sort((a,b)=>a[0]-b[0]);})()`);
    assert.deepEqual(result.map(([frame])=>frame),[0,1,2,3,4,5,6,7]);
    assert.ok(result.every(([,r])=>r.opaque>1000&&r.ready==='true'));
    assert.ok(result.every(([,r])=>r.bottom>=590),'feet must reach the actor floor anchor');
    assert.ok(new Set(result.map(([,r])=>r.hash)).size>1);
  });
  await check('sleep animation renders eight complete frames and pauses without flicker',async()=>{
    await click('[data-zone="rest"]');
    await until(()=>evaluate('document.querySelector("#room-character").dataset.action === "rest"'),'sleep pose');
    const result=await evaluate(`(async()=>{const canvas=document.querySelector('#room-character'),ctx=canvas.getContext('2d'),seen=new Map(),end=performance.now()+6500;
      while(performance.now()<end&&seen.size<8){const frame=Number(canvas.dataset.frame);if(!seen.has(frame)){const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;let opaque=0,edge=0,hash=2166136261,left=canvas.width,right=0,bottom=0;for(let i=0;i<pixels.length;i+=4){const x=i/4%canvas.width,y=Math.floor(i/4/canvas.width);if(pixels[i+3]>128){opaque++;left=Math.min(left,x);right=Math.max(right,x);bottom=y;if(x===0||x===canvas.width-1||y===0||y===canvas.height-1)edge++;}for(let k=0;k<4;k++)hash=Math.imul(hash^pixels[i+k],16777619);}seen.set(frame,{opaque,edge,hash,bottom,centre:(left+right)/2,ready:canvas.dataset.assetReady});}await new Promise(r=>setTimeout(r,80));}
      return [...seen].sort((a,b)=>a[0]-b[0]);})()`);
    assert.deepEqual(result.map(([frame])=>frame),[0,1,2,3,4,5,6,7]);
    assert.ok(result.every(([,r])=>r.opaque>1000&&r.ready==='true'));
    assert.ok(result.every(([,r])=>r.edge===0),'pillow, hair and tail must not be clipped by the animation canvas');
    assert.equal(new Set(result.map(([,r])=>r.hash)).size,8);
    const bottoms=result.map(([,r])=>r.bottom),centres=result.map(([,r])=>r.centre);
    assert.ok(Math.max(...bottoms)-Math.min(...bottoms)<=3,'sleeping frames must stay on the same cushion baseline');
    assert.ok(Math.max(...centres)-Math.min(...centres)<=3,'sleeping frames must not drift sideways');
    await click('#room-motion');await pause(80);
    const still=await evaluate('document.querySelector("#room-character").toDataURL()');
    await pause(750);
    assert.equal(await evaluate('document.querySelector("#room-character").dataset.frame'),'0');
    assert.equal(await evaluate('document.querySelector("#room-character").toDataURL()'),still);
    await screenshot('room-rest.png',true);
    await click('#room-motion');
  });
  await check('room keeps malicious record text literal and marks stale data',async()=>{
    const original=await evaluate('fetch("/api/room").then(r=>r.json())');
    const hostile={...original,mode:'activity',connection:'stale',goals:[],decisions:[{...original.decisions[0],reason:'<img src=x onerror="window.ROOM_INJECTED=1">'}]};
    hostile.projects.items[0].title='<img src=x onerror="window.ROOM_INJECTED=1">';
    hostile.projects.items[0].stages[0].review.reflection='<svg onload="window.ROOM_INJECTED=1">';
    hostile.projects.items[0].stages[0].review_state='needs_recheck';
    await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/room',requestStage:'Request'}]});
    const handler=async event=>{const msg=JSON.parse(event.data);if(msg.method==='Fetch.requestPaused')await cdp('Fetch.fulfillRequest',{requestId:msg.params.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(hostile)).toString('base64')});};
    socket.addEventListener('message',handler);
    await cdp('Page.reload');await waitText('#room-source','旧快照');
    assert.equal(await evaluate('document.querySelector("#journal-content").textContent.includes("<img")'),true);
    assert.equal(await evaluate('window.ROOM_INJECTED || document.querySelectorAll("#journal-content img").length || 0'),0);
    await click('[data-zone="shelf"]');await click('.project-stage summary');
    await waitText('#project-source-note','旧快照');await waitText('#project-reading','需重新核对');
    assert.equal(await evaluate('document.querySelector(".review-leaf").textContent.includes("<svg")'),true);
    assert.equal(await evaluate('window.ROOM_INJECTED || document.querySelectorAll("#project-library img,#project-library svg").length || 0'),0);
    await cdp('Fetch.disable');socket.removeEventListener('message',handler);
    await cdp('Page.reload');await waitText('#room-source','虚构演示');
  });
  await check('room mobile layout and reduced motion remain usable',async()=>{
    await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    await pause(200);assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    assert.equal(await evaluate('getComputedStyle(document.querySelector(".clouds")).animationName'),'none');
    assert.equal(await evaluate('document.querySelector("#room-character").dataset.frame'),'0');
    await click('[data-zone="shelf"]');await waitText('#journal-content','深海来信');
    await click('.project-stage summary');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    assert.equal(await evaluate('getComputedStyle(document.querySelector(".project-book")).transitionDuration'),'0s');
    await screenshot('room-mobile.png',true);
  });
  await check('60-second LivingMemory sync preserves view and scope, skips hidden and disabled pages',async()=>{
    // Only synthetic data: intercept the read-only source boundary, never use SSH.
    let syncs=0,projects=0;
    const info={enabled:true,state:'ready',version:'synthetic-test',synced_at:new Date().toISOString(),counts:{documents_loaded:32,atoms_loaded:0,inactive_documents:0,inactive_atoms:0,unscoped:0,orphan_or_scope_mismatch_atoms:0,invalid_records:0,capacity_omitted:0}};
    const injection=await cdp('Page.addScriptToEvaluateOnNewDocument',{source:'const originalInterval=window.setInterval;window.setInterval=function(fn,ms,...args){if(ms===60000)window.__autoSyncTick=fn;return originalInterval(fn,ms,...args);};'});
    await cdp('Fetch.enable',{patterns:['/api/snapshot','/api/project','/api/livingmemory/refresh'].map(p=>({urlPattern:'*'+p,requestStage:'Request'}))});
    const handler=async event=>{const m=JSON.parse(event.data);if(m.method!=='Fetch.requestPaused')return;const p=m.params,pathName=new URL(p.request.url).pathname;let data;
      if(pathName==='/api/livingmemory/refresh'){syncs++;await pause(220);info.synced_at=new Date().toISOString();data=await (await fetch(base+'/api/snapshot')).json();}
      else{const opts=pathName==='/api/project'?{method:'POST',headers:{'Content-Type':'application/json','X-Observatory':'local'},body:p.request.postData}:{};data=await(await fetch(base+pathName,opts)).json();if(pathName==='/api/project')projects++;}
      await cdp('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify({...data,demo:false,connection:info})).toString('base64')});};
    socket.addEventListener('message',handler);
    try{
      await cdp('Page.navigate',{url:base});await waitText('#dataset-badge','真实只读');
      assert.equal(await evaluate('typeof window.__autoSyncTick'),'function');
      await evaluate('document.querySelector("#days").value=30;document.querySelector("#days").dispatchEvent(new Event("input",{bubbles:true}))');await waitText('#day-label','30');await pause(200);
      await click('[data-kind="knowledge"]');await click('[data-view="timeline"]');
      const before=await evaluate('({title:document.querySelector(".detail-title").textContent,scope:document.querySelector("#scope").value})');
      const oldProjects=projects;await evaluate('window.__autoSyncTick();void 0');await until(()=>syncs===1&&projects>oldProjects,'mock auto source refreshed');await pause(100);
      assert.equal(await evaluate('document.querySelector("#days").value'),'30');assert.equal(await evaluate('document.querySelector("#timeline-view").hidden'),false);
      assert.equal(await evaluate('document.querySelector("[data-kind=knowledge]").getAttribute("aria-pressed")'),'false');
      assert.deepEqual(await evaluate('({title:document.querySelector(".detail-title").textContent,scope:document.querySelector("#scope").value})'),before);
      // A scope switch while the network read is pending must win.
      await evaluate('window.__autoSyncTick();document.querySelector("#scope").value="演示 · 群聊隔离";document.querySelector("#scope").dispatchEvent(new Event("change",{bubbles:true}));void 0');
      await waitText('#count-total','03');await pause(450);assert.equal(await evaluate('document.querySelector("#scope").value'),'演示 · 群聊隔离');
      await evaluate('Object.defineProperty(document,"visibilityState",{configurable:true,get:()=>"hidden"});window.__autoSyncTick();void 0');await pause(250);assert.equal(syncs,2);
      await evaluate('delete document.visibilityState;document.querySelector("#auto-sync").checked=false;document.querySelector("#auto-sync").dispatchEvent(new Event("change"));window.__autoSyncTick();void 0');await pause(250);assert.equal(syncs,2);
    }finally{await cdp('Fetch.disable');socket.removeEventListener('message',handler);await cdp('Page.removeScriptToEvaluateOnNewDocument',{identifier:injection.identifier});}
  });
  await check('no uncaught browser JavaScript exceptions',async()=>assert.deepEqual(errors,[]));
  }
} finally {
  if(socket)socket.close();chrome.kill();if(ownedServer)ownedServer.kill();
  await writeFile(path.join(output,'results.json'),JSON.stringify({base,checks,failures,errors},null,2));
  console.log(`Artifacts: ${output}`);
}
assert.equal(failures.length,0,JSON.stringify(failures));
