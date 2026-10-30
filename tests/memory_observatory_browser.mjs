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
  });
  await check('kind islands separate types and selected edges are emphasized',async()=>{
    const result=await evaluate(`(async()=>{const {layoutByKind}=await import('/graph.js');const d=await fetch('/api/snapshot').then(r=>r.json());const l=layoutByKind(d.memories,800,540);return {groups:l.groups,nodes:l.nodes.map(n=>({kind:n.kind,x:n.px,y:n.py}))};})()`);
    assert.equal(result.groups.length,4);
    for(const n of result.nodes){const g=result.groups.find(g=>g.kind===n.kind);assert.ok(n.x>g.x&&n.x<g.x+g.w&&n.y>g.y&&n.y<g.y+g.h);}
    assert.ok(Number(await evaluate('document.querySelector("#constellation").dataset.highlightedEdges'))>0);
    assert.equal(await evaluate('document.querySelector("#selection-summary").textContent.includes("直接关联")'),true);
  });
  await check('future simulation fades detail but preserves source confidence',async()=>{
    const before=await evaluate('[...document.querySelectorAll(".detail-score strong")].map(x=>x.textContent)');
    await evaluate('document.querySelector("#days").value=180;document.querySelector("#days").dispatchEvent(new Event("input",{bubbles:true}))');
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
  await check('room keeps malicious record text literal and marks stale data',async()=>{
    const original=await evaluate('fetch("/api/room").then(r=>r.json())');
    const hostile={...original,mode:'activity',connection:'stale',goals:[],decisions:[{...original.decisions[0],reason:'<img src=x onerror="window.ROOM_INJECTED=1">'}]};
    await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/room',requestStage:'Request'}]});
    const handler=async event=>{const msg=JSON.parse(event.data);if(msg.method==='Fetch.requestPaused')await cdp('Fetch.fulfillRequest',{requestId:msg.params.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(hostile)).toString('base64')});};
    socket.addEventListener('message',handler);
    await cdp('Page.reload');await waitText('#room-source','旧快照');
    assert.equal(await evaluate('document.querySelector("#journal-content").textContent.includes("<img")'),true);
    assert.equal(await evaluate('window.ROOM_INJECTED || document.querySelectorAll("#journal-content img").length || 0'),0);
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
