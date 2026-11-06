import {RoomScene} from './room-scene.js';
import {renderProjectShelf} from './room-projects.js';
const scene=new RoomScene();
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const ACTION={research:'研究',writing:'写作',explore:'探索',rest:'休息',project_review:'项目回看'};
const STATUS={ready:'准备继续',running:'记录为进行中',paused:'暂存，待继续',waiting_approval:'等待主人点头',blocked:'暂时受阻',completed:'已完成',cancelled:'已取消',draft:'草稿',active:'进行中',write:'写作',research:'资料收集'};
const STATE={disabled:'自主生活尚未开启',paused:'自主活动已暂停',recorded_running:'账本记录：这一轮进行中',unconfirmed:'进行中记录已过时，状态待确认',rest:'最近一次选择：先休息',ended:'最近一轮已结束',planned:'已有安排，尚未开始',interrupted:'最近一轮未正常结束',idle:'还没有新的生活安排',unknown:'暂时没有可确认的近况'};
const SYNC_MS=60000,SYNC_KEY='observatory-room-auto-sync';
let snapshot=null,zone='overview',busy=false,toastTimer,syncTimer,fetchedAt=0,configured=false,lastAttempt=performance.now(),autoSync=true,journalSignature=null;
try{autoSync=localStorage.getItem(SYNC_KEY)!=='false';}catch{/* Storage may be disabled; the in-page switch still works. */}
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;}
function date(t){return typeof t==='number'&&Number.isFinite(t)?new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'时间未记录';}
function toast(text){clearTimeout(toastTimer);$('#room-toast').textContent=text;$('#room-toast').hidden=false;toastTimer=setTimeout(()=>$('#room-toast').hidden=true,5000);}
async function request(path,body){const r=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Observatory':'local'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error(v.error||'无法读取活动记录');return v;}
function row(title,status,text,at,id){const item=node('article','journal-item'),top=node('div','item-top');if(id!==undefined)item.dataset.recordId=id;top.append(node('h3','',title));if(status)top.append(node('span','tag',status));item.append(top);if(text)item.append(node('p','entry-text',text));if(at)item.append(node('div','entry-time',date(at)));return item;}
function rhythmNote(s){
  const box=node('div','next-page rhythm-note'),r=s.decisions[0]?.rhythm;
  box.append(node('span','entry-label','生活节律'));
  if(!r){box.append(node('p','entry-note',s.flags?.life_rhythm_enabled?'还没有新的节律选择记录。':'节律策略尚未开启，继续沿用原有生活安排。'));return box;}
  const phase={morning:'晨间续篇',day:'白日探索',wind_down:'睡前整理',sleep:'留给休息',rest_after_activity:'活动后的留白'};
  box.append(node('h3','entry-title',phase[r.phase]||'上次的节律'),node('p','entry-text',r.reason),node('p','entry-time',`选择当时 · ${date(r.at)}`));
  if(Number.isFinite(r.recent_activity_minutes)&&Number.isFinite(r.window_minutes))box.append(node('p','entry-note',`当时近 ${r.window_minutes} 分钟内，记录到 ${r.recent_activity_minutes} 分钟活动。`));
  if(r.next_eligible_at)box.append(node('p','entry-note',`当时预计 ${date(r.next_eligible_at)} 后可再考虑，仍须满足原有活动条件。`));
  if(!s.flags?.life_rhythm_enabled)box.append(node('p','entry-note','节律策略现已关闭，上方保留历史选择。'));
  return box;
}
function syncControls(){
  $('#room-refresh').disabled=busy||snapshot?.mode==='demo'||snapshot?.connection==='unconfigured';
  $('#room-refresh').textContent=busy?'↻ 正在同步…':'↻ 同步近况';
  $('#room-auto-sync').checked=autoSync;$('#room-auto-sync').disabled=!configured;
  $('#room-sync-state').textContent=!configured?'连接活动账本后可自动同步':!autoSync?'已切换为手动同步':document.hidden?'页面隐藏，自动同步已暂停':'每 60 秒只读同步';
}
function render(preserve=false){
  // Capture at response time, so navigation while a request is pending wins.
  const active=document.activeElement,journal=$('#journal-content'),top=journal.scrollTop,x=scrollX,y=scrollY;
  const list=journal.querySelector('.record-list'),listTop=list?.scrollTop;
  const entry=preserve&&list?[...list.children].find(n=>n.dataset.recordId&&n.getBoundingClientRect().bottom>list.getBoundingClientRect().top):null;
  const entryKey=entry?.dataset.recordId,entryTop=entry?entry.getBoundingClientRect().top-list.getBoundingClientRect().top:null;
  const focus=active?.matches('.project-book,.shelf-index-link')?{kind:active.className,id:active.dataset.project||active.dataset.projectLink}:
    active?.matches('.project-stage summary')?{kind:'stage',id:active.closest('.project-stage').dataset.stageKey}:null;
  // On narrow screens the journal list expands into the page instead of
  // scrolling inside its own box, so its visible entry needs a page anchor too.
  const pageEntries=list&&list.scrollHeight<=list.clientHeight?[...list.children]:[];
  const anchor=preserve?[...document.querySelectorAll('.project-stage'),...pageEntries].find(n=>n.getBoundingClientRect().bottom>0&&n.getBoundingClientRect().top<innerHeight):null;
  const anchorStage=!!anchor?.dataset.stageKey,anchorKey=anchor?.dataset.stageKey||anchor?.dataset.recordId,anchorTop=anchor?.getBoundingClientRect().top;
  renderContent();
  if(!preserve)return;
  if(focus&&!active.isConnected){
    const match=focus.kind==='stage'?[...document.querySelectorAll('.project-stage')].find(n=>n.dataset.stageKey===focus.id)?.querySelector('summary'):
      [...document.querySelectorAll(`.${focus.kind}`)].find(n=>(n.dataset.project||n.dataset.projectLink)===focus.id);
    (match||$('#project-library-title'))?.focus({preventScroll:true});
  }
  journal.scrollTop=top;
  const nextList=journal.querySelector('.record-list');
  if(nextList&&list){
    nextList.scrollTop=listTop;
    const nextEntry=entryKey?[...nextList.children].find(n=>n.dataset.recordId===entryKey):null;
    if(nextEntry)nextList.scrollTop+=nextEntry.getBoundingClientRect().top-nextList.getBoundingClientRect().top-entryTop;
  }
  const next=anchorKey?[...document.querySelectorAll(anchorStage?'.project-stage':'.record-list .journal-item')].find(n=>(anchorStage?n.dataset.stageKey:n.dataset.recordId)===anchorKey):null;
  window.scrollTo({left:x,top:next?scrollY+next.getBoundingClientRect().top-anchorTop:y,behavior:'instant'});
}
function renderContent(){
  if(!snapshot)return;
  const s=snapshot,stale=s.connection==='stale',demo=s.mode==='demo',unavailable=s.mode==='unavailable';
  document.body.dataset.source=s.connection;
  $('#room-source').textContent=demo?'虚构演示场景':stale?'旧快照 · 当前状态未知':unavailable?'活动来源未就绪':'真实活动账本 · 只读';
  $('#room-synced').textContent=s.as_of?`快照 ${date(s.as_of)} · 上海时间`:'未连接时，不补写虚构生活';
  syncControls();
  $('#room-goals').textContent=unavailable?'—':String(s.goals.length).padStart(2,'0');$('#room-works').textContent=unavailable?'—':String(s.works.length).padStart(2,'0');$('#room-pending').textContent=unavailable?'—':String(s.pending).padStart(2,'0');
  $('#room-notice').textContent=s.notice;$('#scene-status').textContent=stale?'旧记录，仅供回看':STATE[s.state]||STATE.unknown;
  const titles={overview:'今天的小天地',desk:'书桌上的下一页',shelf:'慢慢攒下的作品',window:'那些小小的足迹',rest:'安静，也是一种安排'};
  $('#journal-title').textContent=titles[zone];$('#journal-index').textContent=`0${['desk','shelf','window','rest'].indexOf(zone)+1} / 04`;
  $$('button[data-zone]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.zone===zone)));$$('[data-art]').forEach(g=>g.classList.toggle('selected',g.dataset.art===zone));
  const {as_of,age_seconds,...content}=s,signature=JSON.stringify([zone,content]);
  if(signature===journalSignature)return;
  journalSignature=signature;
  const box=$('#journal-content');box.replaceChildren();
  $('#project-library').hidden=zone!=='shelf'||unavailable;
  if(stale)box.append(node('p','state-warning','同步中断或快照已超过三分钟。下面是旧记录，不能据此判断她现在正在做什么。'));
  if(unavailable){box.append(node('p','empty','房间已经在这里，但还没有连接到她的活动账本。连接后，安排、作品和足迹才会在这里出现。'),node('p','entry-note','这是独立的生活观测空间，不会从记忆片段猜测她的当前状态。'));return;}
  const latest=s.decisions[0];
  if(zone==='overview'||zone==='rest')box.append(rhythmNote(s));
  if(zone==='overview'){
    const hello=node('div','overview-note');hello.append(node('h3','','先在这里坐一会儿'),node('p','entry-text','去书桌写一点安排，去书架翻几页书，或是陪她看一会儿窗外。'),node('p','entry-note','点击下方的角落，镜头会带你走近。'));
    box.append(hello,node('h3','entry-title',STATE[s.state]||STATE.unknown));
    if(latest)box.append(node('p','entry-text',latest.reason||'这次选择没有附理由。'),node('p','entry-note',latest.run_status==='completed'?'该轮已返回，不等于目标完成。':'实际进展以活动记录为准。'),node('p','entry-time',date(latest.at)));
  }else if(zone==='desk'){
    box.append(node('span','entry-label',demo?'FICTIONAL DEMO · 虚构演示':'ACTIVITY SNAPSHOT · 活动快照'));
    box.append(node('h3','entry-title',STATE[s.state]||STATE.unknown));
    if(latest){box.append(node('p','entry-text',latest.reason||'这次选择没有附理由。'),node('p','entry-note',`最近选择了${ACTION[latest.action]||'一项活动'}；${latest.run_status==='completed'?'该轮已返回，不等于目标完成。':latest.run_status==='rested'?'只记录休息，没有运行 Agent。':'执行结果以活动账本为准。'}`),node('p','entry-time',`${date(latest.at)} · 选择记录 ${latest.id}`));}
    else box.append(node('p','empty','还没有选择记录。没有安排不等于正在睡觉。'));
    const g=s.goals.find(g=>latest&&g.id===latest.goal_id)||s.goals.find(g=>!['completed','cancelled'].includes(g.status));
    if(g){const next=node('div','next-page');next.append(node('span','entry-label','留在这里，下一次继续'),node('h3','entry-title',g.title),node('p','entry-text',g.next_step||'还没有记录下一步。'),node('p','entry-note',STATUS[g.status]||'状态未识别'));box.append(next);}
  }else if(zone==='shelf'){
    box.append(renderProjectShelf(s));
    box.append(node('p','entry-note','这里展示已有作品的标题与版本；放在书架上，不代表内容已经核验完成。'));
    const list=node('div','record-list');for(const w of s.works)list.append(row(w.title,STATUS[w.status]||w.status,`第 ${w.revision??'?'} 版 · ${STATUS[w.phase]||w.phase}`,w.updated_at,w.id));
    box.append(list);if(!s.works.length)box.append(node('p','empty','书架还空着。等有真正保存下来的作品，再把它放上来。'));
  }else if(zone==='window'){
    box.append(node('p','entry-note','按实际选择记录倒序展示。这是她留下的安排，不是对经历的补写。'));
    const list=node('div','record-list');for(const d of s.decisions)list.append(row(ACTION[d.action]||'安排',d.run_status==='completed'?'本轮结束':d.run_status==='rested'?'未启动活动':d.run_status==='not_started'?'尚未开始':STATUS[d.run_status]||d.run_status,d.reason,d.at,d.id));box.append(list);if(!s.decisions.length)box.append(node('p','empty','窗外很安静，还没有可以回看的安排。'));
  }else{
    const rest=s.decisions.find(d=>d.action==='rest');box.append(node('span','entry-label','SPACE TO BREATHE'),node('p','entry-text',rest?rest.reason:'暂时没有休息选择记录。沙发上的留白只是布景，不代表她正在休息。'));
    if(rest)box.append(node('p','entry-time',`上次休息选择 · ${date(rest.at)}`));
    box.append(node('div','next-page','最近的主动联系'));
    const result={sent:'有消息 ID',reported:'发送器报告成功',uncertain:'结果待核对',cancelled:'发送前取消',reserved:'尚未派发',dispatching:'派发中',rejected:'发送被拒绝'};
    for(const c of s.contacts.slice(0,3))box.append(row(c.source==='life'?'生活分享':'主动聊天',result[c.status]||'状态未知','',c.created));
    if(!s.contacts.length)box.append(node('p','empty','暂无共享联系记录。没有记录不代表两个入口都已接入协调。'));
    box.append(node('p','entry-note','这里不模拟体力、情绪或亲密度；是否联系主人仍由原来的开关、免打扰和额度决定。'));
  }
}
for(const b of $$('button[data-zone]'))b.addEventListener('click',()=>{if(b.dataset.zone===zone)return;zone=b.dataset.zone;scene.go(zone);render();});
const reduced=matchMedia('(prefers-reduced-motion: reduce)');let motionOff=reduced.matches;
function motion(){document.body.classList.toggle('motion-off',motionOff);scene.setPaused(motionOff);$('#room-motion').setAttribute('aria-pressed',String(motionOff));$('#room-motion').textContent=motionOff?'继续动画':'暂停动画';}
$('#room-motion').addEventListener('click',()=>{motionOff=!motionOff;motion();});motion();reduced.addEventListener('change',e=>{motionOff=e.matches;motion();});
function received(s){
  snapshot=s;fetchedAt=performance.now()/1000;
  configured=s.mode==='activity'||(s.mode==='unavailable'&&s.connection==='unavailable');
}
function autoTick(){void syncRoom(true);}
function scheduleSync(){
  clearTimeout(syncTimer);
  if(!autoSync||!configured||document.hidden||busy)return;
  // Schedule from the last attempt, not a fixed interval. An early callback
  // waits only its remaining milliseconds instead of skipping a whole minute.
  syncTimer=setTimeout(autoTick,Math.max(0,SYNC_MS-(performance.now()-lastAttempt)));
}
async function syncRoom(automatic=false){
  if(busy||snapshot?.mode==='demo'||snapshot?.connection==='unconfigured')return;
  if(automatic&&(!autoSync||!configured||document.hidden||performance.now()-lastAttempt<SYNC_MS)){scheduleSync();return;}
  clearTimeout(syncTimer);busy=true;lastAttempt=performance.now();syncControls();
  try{
    received(await request('/api/room/refresh',{}));
    if(!automatic)toast('已读取活动账本，没有改变她的活动。');
  }catch(e){
    // Keep the last known records and their original age. A failed read is not
    // an empty ledger, a new receipt, or evidence that an activity happened.
    if(snapshot?.mode==='activity')snapshot={...snapshot,connection:'stale'};
    if(!automatic)toast(e.message);
  }finally{busy=false;render(true);scheduleSync();}
}
$('#room-refresh').addEventListener('click',()=>syncRoom());
$('#room-auto-sync').addEventListener('change',()=>{
  autoSync=$('#room-auto-sync').checked;
  try{localStorage.setItem(SYNC_KEY,String(autoSync));}catch{/* Preference remains usable for this page. */}
  syncControls();scheduleSync();
});
try{received(await request('/api/room'));render();}catch{snapshot={mode:'unavailable',connection:'unavailable',state:'unknown',as_of:null,notice:'暂时无法读取来源。房间布景不代表真实活动。'};render();}
// Only the existing read-only activity endpoint is polled. No bot/model tools.
lastAttempt=performance.now();scheduleSync();
document.addEventListener('visibilitychange',()=>{syncControls();if(document.hidden)scheduleSync();else autoTick();});
// The server reports monotonic load age; add time since this page received it. Never compare the remote as_of clock with the browser clock.
setInterval(()=>{if(snapshot?.mode==='activity'){const age=(snapshot.age_seconds||0)+Math.max(0,performance.now()/1000-fetchedAt);if(age>180&&snapshot.connection!=='stale'){snapshot={...snapshot,connection:'stale'};render(true);}}},30000);
