// Read-only shelf. Render ledger text as textContent, never as HTML or commands.
const $=s=>document.querySelector(s);
const STATUS={active:'慢慢进行中',paused:'先放一放',completed:'已经收尾',archived:'收进旧册'};
const GOAL={ready:'留待下一轮',running:'账本记录进行中',paused:'留待继续',completed:'阶段已完成',cancelled:'已取消',blocked:'暂时受阻',waiting_approval:'等待主人点头',pending_creation:'下一阶段待恢复',unavailable:'目标记录不可用'};
const stamp=t=>typeof t==='number'&&Number.isFinite(t)?new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'时间未记录';
const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
let selected=null,current=null,painted=null;
const opened=new Map();
function shelf(s){return s.projects||{state:'legacy',enabled:false,items:[],total:0};}
function choose(id){selected=id;paint();}
function stageView(stage,pid,index,last){
  const detail=node('details','project-stage'),key=`${pid}:${stage.position}`;
  detail.dataset.stageKey=key;
  detail.open=opened.has(key)?opened.get(key):index===last;
  detail.addEventListener('toggle',()=>{if(detail.isConnected)opened.set(key,detail.open);});
  const title=node('summary','stage-heading'),heading=node('span','stage-heading-text');
  heading.append(node('strong','',stage.title),node('small','',GOAL[stage.goal_status]||'状态待核对'));
  title.append(node('span','stage-number',String(stage.position??index+1).padStart(2,'0')),heading,node('span','stage-chevron','⌄'));detail.append(title);
  const body=node('div','stage-content');
  if(stage.next_step)body.append(node('p','stage-next',`下一页 · ${stage.next_step}`));
  const w=stage.work,r=stage.review;
  if(w){
    const work=node('section','work-leaf');work.append(node('span','folio-label',`作品摘页 · 第 ${w.revision??'?'} 版`),node('h4','',w.title));
    work.append(node('p','work-excerpt',w.excerpt||'这一版还没有保存正文。'));
    if(w.excerpt_truncated)work.append(node('p','entry-note','这里只展开前600字；完整作品请向她询问。'));
    work.append(node('p','entry-note',w.sources_available?`记录了 ${w.source_count} 条来源，当前仍可找到；未在此重新核验内容。`:'来源缺失、被修订或尚未记录，需要重新核对。'));
    body.append(work);
  }else body.append(node('p','empty','这一阶段还没有可展示的作品。'));
  if(stage.review_state==='needs_recheck')body.append(node('p','state-warning','回看对应的版本或来源已有变化，以下保留历史文字，需重新核对。'));
  if(stage.review_state==='unavailable')body.append(node('p','state-warning','回看与作品的关联不完整，暂不展示。'));
  if(r){
    const review=node('section','review-leaf');review.append(node('span','folio-label',`完成后的回看 · 第 ${r.revision??'?'} 版 · ${stamp(r.created)}`));
    for(const [label,text] of [['留住的收获',r.reflection],['还不确定的地方',r.limitations],['下一次想弄清',r.next_question]]){
      if(text)review.append(node('h4','',label),node('p','',text));
    }
    if(!r.next_question)review.append(node('p','review-close','这一页先写到这里，没有继续展开的问题。'));
    if(r.truncated)review.append(node('p','entry-note','长篇回看已节选，完整记录请向她询问。'));
    review.append(node('p','entry-note','回看是她留下的解释，不等于结论已被证实。'));body.append(review);
  }else if(stage.review_state==='none')body.append(node('p','entry-note',stage.goal_status==='completed'?'阶段已完成，还没有保存回看。':'等这一阶段完成后，再留下回看。'));
  body.append(node('p','entry-time',`阶段记录 · ${stamp(stage.updated)}`));detail.append(body);return detail;
}
function paint(){
  // A receipt timestamp alone must not replace the page under the reader's
  // cursor (or clear a text selection). Only rebuild when shelf content changes.
  const signature=JSON.stringify([shelf(current),selected,current.mode,current.connection==='stale']);
  if(signature===painted)return;
  // Native toggle events are queued. Capture the DOM before rebuilding so a
  // quick change of book/zone cannot lose a just-opened or just-closed chapter.
  for(const detail of document.querySelectorAll('.project-stage'))opened.set(detail.dataset.stageKey,detail.open);
  const ids=new Set((shelf(current).items||[]).map(i=>i.id));
  for(const key of opened.keys())if(![...ids].some(id=>key.startsWith(id+':')))opened.delete(key);
  painted=signature;
  const s=current,p=shelf(s),books=$('#project-books'),reading=$('#project-reading');books.replaceChildren();reading.replaceChildren();
  $('#project-source-note').textContent=s.mode==='demo'?'虚构演示 · 这些项目和文字不是她的真实经历':s.connection==='stale'?'旧快照 · 以下只供回看，不表示当前进展':'只读活动账本 · 翻页不会启动她的活动';
  if(p.state!=='ready'){
    books.append(node('p','empty',p.state==='unsupported'?'项目记录暂不兼容，已有作品仍可在手记中查看。':p.state==='legacy'?'这个活动账本还没有项目册。等真正保存了项目，再把它放上来。':'还没有连接可用的项目记录。'));return;
  }
  if(!p.enabled)books.append(node('p','project-disabled','项目功能目前关闭，下方保留已有记录。'));
  if(!p.items.length){books.append(node('p','empty','还没有项目册。读到值得持续研究的问题时，可以慢慢写下第一章。'));return;}
  if(!p.items.some(item=>item.id===selected))selected=p.items[0].id;
  painted=JSON.stringify([p,selected,s.mode,s.connection==='stale']);
  for(const [i,item] of p.items.entries()){
    const button=node('button','project-book');button.type='button';button.dataset.project=item.id;button.setAttribute('aria-pressed',String(item.id===selected));button.setAttribute('aria-controls','project-reading');
    button.append(node('span','book-edition',`VOL. ${String(i+1).padStart(2,'0')}`),node('strong','book-title',item.title),node('span','book-state',STATUS[item.status]||'状态待核对'),node('span','book-pages',`${item.stages.length} / ${item.max_stages??'?'} 章 · 翻开看看 ↗`));
    button.addEventListener('click',()=>{const focus=document.activeElement===button;choose(item.id);if(focus)[...books.querySelectorAll('button')].find(b=>b.dataset.project===item.id)?.focus({preventScroll:true});});books.append(button);
  }
  if(p.truncated)books.append(node('p','entry-note',`当前展示 ${p.items.length} 册，共 ${p.total} 册；进行中的项目优先。更多记录可向她询问。`));
  const item=p.items.find(item=>item.id===selected),intro=node('div','project-intro');
  intro.append(node('span','folio-label','这册笔记，为什么开始'),node('h3','',item.title),node('p','',item.why||'还没有记录当时的理由。'),node('span','tag',STATUS[item.status]||'状态待核对'),node('p','entry-time',`最近记录 · ${stamp(item.updated)}`));
  if(item.status==='completed')intro.append(node('p','entry-note','这个项目已经收尾，留下的问题不代表还会自动继续。'));
  const stages=node('div','project-timeline');stages.setAttribute('aria-label','项目各阶段');
  item.stages.forEach((s,i)=>stages.append(stageView(s,item.id,i,item.stages.length-1)));
  if(!item.stages.length)stages.append(node('p','empty','阶段关联尚不完整。'));
  reading.append(intro,stages);
}
export function renderProjectShelf(snapshot){
  current=snapshot;const p=shelf(snapshot);
  paint();
  const index=node('div','shelf-index');index.append(node('span','entry-label','她的项目册'));
  if(!p.items?.length){index.append(node('p','entry-note',p.state==='unsupported'?'项目记录暂不可读。':'还没有可展示的项目册。'));return index;}
  for(const item of p.items){
    const button=node('button','shelf-index-link');button.type='button';button.dataset.projectLink=item.id;button.append(node('span','',item.title),node('small','',STATUS[item.status]||'待核对'));
    button.addEventListener('click',()=>{choose(item.id);$('#project-library').scrollIntoView({behavior:'auto',block:'start'});$('#project-library-title').focus({preventScroll:true});});index.append(button);
  }
  index.append(node('p','entry-note','翻开项目册，看她为什么开始、写下了什么。'));return index;
}
