import {Constellation, COLORS} from './graph.js';

const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const KINDS={episode:'经历',preference:'偏好',knowledge:'知识',method:'方法',document:'文档',plan:'计划',relation:'关系',unclassified:'未分类'};
const state={data:null,selected:undefined,view:'atlas',days:0,query:'',scope:null,rehearsed:new Set(),pinned:new Set(),kinds:new Set(Object.keys(KINDS)),autoSync:true};
let requestSerial=0,sliderTimer,toastTimer,autoSyncing=false;const AUTO_SYNC_MS=60000;
const graph=new Constellation($('#constellation'),selectMemory);
function el(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function icon(name){const node=document.createElementNS('http://www.w3.org/2000/svg','svg');node.setAttribute('class','icon');const use=document.createElementNS(node.namespaceURI,'use');use.setAttribute('href',`#i-${name}`);node.append(use);return node;}
function percent(value){return `${Math.round(value*100)}%`;}
function date(value){return new Date(value).toLocaleDateString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Asia/Shanghai'});}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('#toast').hidden=true;},5000);}
async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Observatory':'local'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(result.error||'请求失败');return result;}
function install(data){
  state.data=data;state.scope=data.scope;
  // `undefined` means "pick the first memory"; null is a deliberate deselect
  // from the star map and must survive 60-second refreshes.
  if(state.selected===undefined)state.selected=data.memories[0]?.id||null;
  else if(state.selected!==null&&!data.memories.some(m=>m.id===state.selected))state.selected=data.memories[0]?.id||null;
  $('#scope').replaceChildren(...data.scopes.map(scope=>{const option=el('option','',data.connection?.scopes?.[scope]?.label||scope);option.value=scope;option.selected=scope===data.scope;return option;}));
  render();
}
async function refresh(){
  const serial=++requestSerial;
  try{const data=await api('/api/project',{scope:state.scope,days:state.days,query:state.query,rehearsed:[...state.rehearsed],pinned:[...state.pinned]});if(serial===requestSerial)install(data);}
  catch(error){if(serial===requestSerial)toast(error.message);}
}
function loadAutoSync(){try{state.autoSync=localStorage.getItem('observatory-auto-sync')!=='0';}catch{state.autoSync=true;}const box=$('#auto-sync');if(box)box.checked=state.autoSync;}
async function syncLive(auto=false){
  if(auto&&(!state.autoSync||document.visibilityState==='hidden'||!state.data?.connection?.enabled))return;
  if(autoSyncing)return;
  autoSyncing=true;
  const button=$('#sync-livingmemory');
  if(!auto){button.disabled=true;button.textContent='正在只读同步…';}
  try{
    await api('/api/livingmemory/refresh',{scope:state.scope});
    ++requestSerial;
    if(!auto){resetExperiment();state.query='';$('#query').value='';}
    // Project once with the CURRENT scope and experiment after a slow read.
    // Installing the refresh response first would flash day-zero / old scope.
    await refresh();
    if(!auto)toast('已重新读取 LivingMemory，原库内容与访问计数未由此操作更新。');
  }catch(error){
    await refresh();
    if(!auto)toast(error.message);
  }finally{
    autoSyncing=false;
    if(!auto){button.disabled=false;button.textContent='同步真实记忆';}
  }
}
function render(){
  const data=state.data;if(!data)return;
  for(const key of ['total','connections','clear','fading'])$(`#count-${key}`).textContent=String(data.stats[key]).padStart(2,'0');
  $('#scope-count').textContent=`${data.stats.total} 个片段`;
  const connection=data.connection,live=Boolean(connection?.enabled);
  $('#dataset-badge').textContent=live?(connection.state==='ready'?'LivingMemory · 真实只读':'LivingMemory · 旧快照'):data.demo?'演示记忆':'导入快照';
  $('#footer-mode').textContent=live?'真实内容 · 曲线与线索检索仍为本地模拟':data.demo?'虚构演示 · 不读取聊天记录':'导入快照 · 仅存在本地服务内存中';
  $('#source-panel').hidden=!live;$('#import-button').hidden=live;$('#reset-dataset').hidden=live;
  $('#connection-about').textContent=live?'已连接 LivingMemory '+connection.version+' 的只读快照。每 60 秒自动同步（仅页面可见，可关闭）或手动同步会重新读取；浏览与实验不更新访问计数、不巩固、不删除原记忆。':'当前未连接 LivingMemory。所有实验只作用于内存快照，浏览不增加访问计数，不巩固、不删除原记忆。页面刷新会清空实验；服务重启会清空导入。';
  if(live){
    const c=connection.counts;
    $('#source-state').textContent=connection.state==='ready'?'已连接真实记忆 · 只读':'同步失败 · 正在显示上次快照';
    $('#source-panel').classList.toggle('stale',connection.state!=='ready');
    $('#source-detail').textContent=`LivingMemory ${connection.version} · ${c.documents_loaded} 篇文档 + ${c.atoms_loaded} 条有效原子 · ${data.scopes.length} 个独立作用域`;
    $('#source-exclusions').textContent=`过滤：${c.inactive_documents} 篇非活跃文档，${c.inactive_atoms} 条非活跃/过期原子；缺少归属或有效父记录：${c.unscoped+c.orphan_or_scope_mismatch_atoms} 条；无效 ${c.invalid_records} 条，超出上限 ${c.capacity_omitted} 条。`;
    $('#source-sync-time').textContent=`快照时间 ${new Date(connection.synced_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · ${state.autoSync?'每 60 秒自动同步':'手动刷新'}`;
  }
  $('#snapshot-date').textContent=`快照 ${date(data.as_of)} · 模拟至 ${date(data.projected_at)}`;
  $('#days').value=state.days;$('#days').style.setProperty('--progress',`${state.days/365*100}%`);
  $('#day-label').textContent=state.days?`向未来 ${state.days} 天`:'快照当日';
  $('#kind-filters').replaceChildren(...Object.entries(KINDS).filter(([kind])=>['episode','preference','knowledge','method'].includes(kind)||data.memories.some(m=>m.kind===kind)).map(([kind,label])=>{const button=el('button','kind-filter');button.dataset.kind=kind;button.setAttribute('aria-pressed',String(state.kinds.has(kind)));button.append(el('i',`dot ${kind}`),document.createTextNode(label),el('span','',data.memories.filter(m=>m.kind===kind).length));return button;}));
  $('#lab-summary').textContent=`模拟第 ${state.days} 天：${data.stats.clear} 段仍然清晰，${data.stats.fading} 段正在淡去，${data.stats.dormant} 段细节已沉入深处。${data.stats.protected} 段被保留。`;
  graph.setData(data,state.selected,state.kinds);renderDetail();renderShelf();renderTimeline();renderTrace();
}
function selectMemory(id){state.selected=id;render();}
function scoreCell(label,value,color){const cell=el('div');cell.append(el('span','',label),el('strong','',value===null?'—':Math.round(value*100)),el('small','',value===null?'未评估':'%'));const track=el('div','score-track');const fill=el('i');fill.style.width=`${(value??0)*100}%`;if(color)fill.style.background=color;track.append(fill);cell.append(track);return cell;}
function curveSVG(memory){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 240 82');svg.setAttribute('class','curve');svg.setAttribute('role','img');svg.setAttribute('aria-label','未来一年细节与意义可及度曲线');
  function line(tag,attrs){const n=document.createElementNS(svg.namespaceURI,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));svg.append(n);return n;}
  for(const y of [9,39,69])line('line',{x1:0,y1:y,x2:240,y2:y,stroke:'#c2d0e4','stroke-width':.5,'stroke-dasharray':'2 4'});
  for(const [key,color,dash] of [['gist_retention','#bdac8b','3 3'],['detail','#789abb','']]){const points=memory.curve.filter(p=>p.day<=370).map(p=>`${p.day/370*240},${69-p[key]*60}`).join(' ');line('polyline',{points,fill:'none',stroke:color,'stroke-width':1.4,'stroke-dasharray':dash});}
  const x=state.days/370*240;line('line',{x1:x,y1:5,x2:x,y2:72,stroke:'#9bb3d5','stroke-width':.6,opacity:.6});line('circle',{cx:x,cy:69-memory.detail*60,r:2.7,fill:'#789abb'});
  return svg;
}
function renderDetail(){
  const target=$('#memory-detail'),m=state.data.memories.find(m=>m.id===state.selected);target.replaceChildren();
  if(!m){target.append(el('p','empty-state','选择一段记忆，开始探索。'));return;}
  $('#memory-index').textContent=String(state.data.memories.indexOf(m)+1).padStart(2,'0');
  const kind=el('div','memory-kind');kind.append(el('i',`dot ${m.kind}`),el('span','',KINDS[m.kind]),el('span','',date(m.created_at)));
  const gist=el('div','gist-box');gist.append(el('small','','留下的意义'),document.createTextNode(m.gist));
  const scores=el('div','detail-score');scores.append(scoreCell('细节可及度 · 模拟',m.detail),scoreCell('来源可信度',m.confidence));
  const curveLabel=el('div','curve-label','时间里的轮廓');curveLabel.append(el('span','',`稳定度 ${m.stability_days} 天${m.stability_assumed?' · 假设值':''}`));
  const key=el('div','curve-key');for(const text of ['细节','意义 · 较慢衰减']){const span=el('span');span.append(el('i'),document.createTextNode(text));key.append(span);}
  const actions=el('div','detail-actions');
  for(const [name,label,set] of [['spark','模拟巩固',state.rehearsed],['pin','模拟保留',state.pinned]]){
    const button=el('button');button.append(icon(name),document.createTextNode(set.has(m.id)?`${label}中`:label));button.setAttribute('aria-pressed',String(set.has(m.id)));button.disabled=name==='pin'&&m.pinned;
    if(button.disabled){button.textContent='原记录已保留';button.title='源快照中的保留状态在此处不可取消';}
    button.addEventListener('click',()=>{if(set.has(m.id))set.delete(m.id);else set.add(m.id);refresh();});actions.append(button);
  }
  const source=el('div','source-box');source.append(el('span','','记忆来源'),document.createTextNode(m.source),el('small','',`作用域：${state.data.connection?.scopes?.[m.scope]?.label||m.scope} · 可信度来自来源字段`));
  target.append(kind,el('h2','detail-title',m.title),el('p','detail-text',m.text),gist,scores,curveLabel,curveSVG(m),key,actions,el('p','experiment-note','实验仅改变当前视图，浏览不会强化记忆。'),source);
  if(m.upstream){const u=m.upstream;const facts=el('div','upstream-facts');facts.append(el('h3','','原库记录 · 观测值'));const lines=[`类型：${u.atom_type||u.record_type} · 状态：${u.status}`];if(u.access_count!==undefined)lines.push(`原库访问次数：${u.access_count}（此页面不增加）`);if(u.reinforcement_count!==undefined)lines.push(`原库强化次数：${u.reinforcement_count}`);if(u.ttl_days!==undefined)lines.push(`原库 TTL：${u.ttl_days} 天；不是模拟半衰期`);if(u.expires_at)lines.push(`原库过期时间：${date(u.expires_at)}`);if(u.atom_type==='planned')lines.push('这是计划，不代表已经发生。');if(u.truncated)lines.push('正文超过展示上限，当前仅展示前 4,000 字符。');if(!u.access_time_known)lines.push('缺少可用的访问时间；模拟年龄暂从创建时间计算。');for(const line of lines)facts.append(el('p','',line));if(u.relations?.length){facts.append(el('h3','','原图谱关系 · 来源抽取，非事实核验'));for(const relation of u.relations)facts.append(el('p','',`${relation.source} → ${relation.relation} → ${relation.target}`));}target.append(facts);}
  const related=state.data.edges.filter(e=>e.source===m.id||e.target===m.id).slice(0,3);
  if(related.length)target.append(el('h3','related-title','沿着微光，继续想起'));
  for(const edge of related){const other=state.data.memories.find(v=>v.id===(edge.source===m.id?edge.target:edge.source));if(!other)continue;const button=el('button','related-item');const text=el('span','',other.title);text.append(el('small','',edge.inferred?'共同主题 · 推导联想':edge.label));button.append(text,icon('arrow'));button.addEventListener('click',()=>selectMemory(other.id));target.append(button);}
}
function visibleMemories(){return state.data.memories.filter(m=>state.kinds.has(m.kind));}
function renderShelf(){
  const list=$('#memory-list');list.replaceChildren();const memories=visibleMemories().sort((a,b)=>b.importance-a.importance);
  $('#shelf-count').textContent=String(memories.length).padStart(2,'0');
  for(const m of memories){const button=el('button',`memory-card${m.id===state.selected?' selected':''}`);button.setAttribute('aria-pressed',String(m.id===state.selected));const head=el('div','memory-card-head');head.append(el('i',`dot ${m.kind}`),el('span','',KINDS[m.kind]),el('span','',m.protected?'已保留':`可及 ${percent(m.detail)}`));button.append(head,el('h3','',m.title),el('p','',m.gist));button.addEventListener('click',()=>selectMemory(m.id));list.append(button);}
  if(!memories.length)list.append(el('p','empty-state','所有类型已隐藏。打开左侧类型筛选，重新看见记忆。'));
}
function renderTimeline(){
  const list=$('#timeline-list');list.replaceChildren();for(const m of visibleMemories().sort((a,b)=>b.created_at.localeCompare(a.created_at))){const item=el('article','timeline-item');const time=el('time','',date(m.created_at));time.dateTime=m.created_at;const button=el('button');button.append(el('strong','',m.title),el('p','',m.gist));button.addEventListener('click',()=>selectMemory(m.id));item.append(time,button);list.append(item);}
}
function renderTrace(){
  $('#recall-trace').hidden=!state.query;const list=$('#trace-list');list.replaceChildren();
  for(const t of state.data.trace){const m=state.data.memories.find(m=>m.id===t.id),via=state.data.memories.find(m=>m.id===t.via);const button=el('button','trace-item');button.append(el('strong','',m.title),el('small','',via?`${via.title} → ${t.reason}`:t.reason));button.addEventListener('click',()=>selectMemory(t.id));list.append(button);}
  if(state.query&&!state.data.trace.length)list.append(el('div','empty-state','没有找到相关片段。试试更具体的主题、人物或场景。'));
}
function setView(view){
  state.view=view;for(const button of $$('.nav-item[data-view]')){const active=button.dataset.view===view;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}
  $('#graph-stage').hidden=view!=='atlas';$('#timeline-view').hidden=view!=='timeline';$('#lab-view').hidden=view!=='lab';$('.view-options').hidden=view!=='atlas';
  const content={atlas:['每一次想起，都有回响','让经历连成星海，看见记忆如何沉淀，又被轻轻唤起。','记忆星海'],timeline:['走过的日子，都有微光','沿着时间，重新遇见那些留下意义的片段。','共同经历'],lab:['有些淡去，是为了留下','用时间、巩固与保留，观察记忆的不同轨迹。','遗忘实验室']}[view];
  $('#page-title').replaceChildren(document.createTextNode(content[0]),el('span','','。'));$('#page-description').textContent=content[1];$('#view-label').textContent=content[2];if(view==='atlas')requestAnimationFrame(()=>graph.resize());
}
function resetExperiment(){state.days=0;state.rehearsed.clear();state.pinned.clear();}
for(const button of $$('.nav-item[data-view]'))button.addEventListener('click',()=>setView(button.dataset.view));
$('#kind-filters').addEventListener('click',event=>{const button=event.target.closest('.kind-filter');if(!button)return;const kind=button.dataset.kind;if(state.kinds.has(kind))state.kinds.delete(kind);else state.kinds.add(kind);render();});
$('#scope').addEventListener('change',()=>{state.scope=$('#scope').value;state.selected=undefined;graph.clearScope();resetExperiment();refresh();});
$('#search-form').addEventListener('submit',e=>{e.preventDefault();state.query=$('#query').value.trim();refresh();});
$('#clear-query').addEventListener('click',()=>{state.query='';$('#query').value='';refresh();});
$('#days').addEventListener('input',()=>{state.days=Number($('#days').value);$('#day-label').textContent=`向未来 ${state.days} 天`;$('#days').style.setProperty('--progress',`${state.days/365*100}%`);graph.previewDays(state.days);clearTimeout(sliderTimer);sliderTimer=setTimeout(refresh,70);});
$('#reset-simulation').addEventListener('click',()=>{clearTimeout(sliderTimer);resetExperiment();refresh();});
$('#dimension').addEventListener('click',()=>{graph.twoD=!graph.twoD;$('#dimension').textContent=graph.twoD?'2D 阅读':'3D 空间';$('#dimension').setAttribute('aria-label',graph.twoD?'切换三维空间视图':'切换二维阅读视图');graph.draw(performance.now());});
function motionLabel(){const button=$('#motion');button.textContent=graph.paused?'▷':'Ⅱ';button.setAttribute('aria-label',graph.paused?'播放星海动画':'暂停星海动画');button.title=button.getAttribute('aria-label');}
$('#motion').addEventListener('click',()=>{graph.paused=!graph.paused;motionLabel();});motionLabel();
$('#zoom-in').addEventListener('click',()=>graph.zoomBy(1.15));$('#zoom-out').addEventListener('click',()=>graph.zoomBy(1/1.15));$('#recenter').addEventListener('click',()=>graph.recenter());
$('#about-button').addEventListener('click',()=>$('#info-dialog').showModal());$('#import-button').addEventListener('click',()=>{$('#import-error').textContent='';$('#import-file').value='';$('#import-dialog').showModal();});
for(const button of $$('.dialog-close, .dialog-dismiss'))button.addEventListener('click',()=>button.closest('dialog').close());
$('#import-file').addEventListener('change',async()=>{
  const file=$('#import-file').files[0];if(!file)return;$('#import-error').textContent='';
  try{if(file.size>4*1024*1024)throw new Error('文件超过 4 MB，请缩小导出范围。');const payload=JSON.parse(await file.text());const data=await api('/api/import',payload);++requestSerial;resetExperiment();state.query='';$('#query').value='';state.selected=undefined;state.kinds=new Set(Object.keys(KINDS));install(data);$('#import-dialog').close();toast('快照已载入本地内存。源文件与真实记忆均未修改。');}
  catch(error){$('#import-error').textContent=error instanceof SyntaxError?'文件不是有效的 JSON。':error.message;}
});
$('#reset-dataset').addEventListener('click',async()=>{try{const data=await api('/api/reset',{});++requestSerial;resetExperiment();state.query='';$('#query').value='';state.selected=undefined;state.kinds=new Set(Object.keys(KINDS));install(data);toast('已返回虚构演示数据。');}catch(error){toast(error.message);}});
$('#sync-livingmemory').addEventListener('click',()=>syncLive(false));
loadAutoSync();
try{install(await api('/api/snapshot'));}catch(error){toast(`无法连接本地服务：${error.message}`);$('#memory-detail').append(el('p','empty-state','请确认本地 Python 服务仍在运行，然后刷新页面。'));}
$('#auto-sync').addEventListener('change',()=>{state.autoSync=$('#auto-sync').checked;try{localStorage.setItem('observatory-auto-sync',state.autoSync?'1':'0');}catch{}render();});
setInterval(()=>syncLive(true),AUTO_SYNC_MS);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')syncLive(true);});
