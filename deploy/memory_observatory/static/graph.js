// Stable type islands. Positions are display layout, never semantic embeddings.
export const COLORS={episode:'#6e9eab',preference:'#c39b58',knowledge:'#688dc9',method:'#9a85c2',document:'#7c9dac',plan:'#cb9472',relation:'#b886b2',unclassified:'#8994a9'};
export const NAMES={episode:'经历',preference:'偏好',knowledge:'知识',method:'方法',document:'文档',plan:'计划',relation:'关系',unclassified:'未分类'};
const TAU=Math.PI*2,ORDER=Object.keys(COLORS);
function hash(s){let n=2166136261;for(const c of s)n=Math.imul(n^c.charCodeAt(0),16777619);return(n>>>0)/4294967296;}
function rgba(hex,a){return 'rgba('+parseInt(hex.slice(1,3),16)+','+parseInt(hex.slice(3,5),16)+','+parseInt(hex.slice(5,7),16)+','+Math.max(0,Math.min(1,a))+')';}
export function layoutByKind(memories,width,height){
 const kinds=ORDER.filter(k=>memories.some(m=>m.kind===k)),cols=Math.min(kinds.length||1,width<480?2:kinds.length>4?3:2),rows=Math.ceil((kinds.length||1)/cols);
 const margin=24,top=102,bottom=58,cw=(width-margin*2)/cols,ch=(height-top-bottom)/rows;
 const groups=kinds.map((kind,i)=>({kind,x:margin+(i%cols)*cw,y:top+Math.floor(i/cols)*ch,w:cw,h:ch}));
 const nodes=[];for(const g of groups){const list=memories.filter(m=>m.kind===g.kind).sort((a,b)=>a.id.localeCompare(b.id));
  list.forEach((m,i)=>{const angle=i*2.399963+hash(g.kind),radius=Math.sqrt((i+.5)/list.length);nodes.push({...m,px:g.x+g.w*.5+Math.cos(angle)*radius*g.w*.36,py:g.y+g.h*.57+Math.sin(angle)*radius*g.h*.31,pz:(hash(m.id)-.5)*8});});}
 return {groups,nodes};
}
export class Constellation{
 constructor(canvas,onSelect){
  this.canvas=canvas;this.ctx=canvas.getContext('2d');this.onSelect=onSelect;this.data={memories:[],edges:[],trace:[]};this.nodes=[];this.positions=[];this.groups=[];this.selected=null;this.zoom=1;this.rotation=0;this.tilt=0;this.panX=0;this.panY=0;this.twoD=false;
  this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;this.paused=this.reduced;this.visibleKinds=new Set(ORDER);this.hover=null;this.drag=null;this.tooltip=document.querySelector('#graph-tooltip');
  new ResizeObserver(()=>this.resize()).observe(canvas);
  canvas.addEventListener('pointerdown',e=>{this.drag={x:e.clientX,y:e.clientY,moved:0};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>this.move(e));canvas.addEventListener('pointerup',e=>{if(this.drag&&this.drag.moved<5){const p=this.hit(e);if(p)this.onSelect(p.id);}this.drag=null;});
  canvas.addEventListener('pointercancel',()=>this.drag=null);canvas.addEventListener('pointerleave',()=>{this.hover=null;if(this.tooltip)this.tooltip.hidden=true;});
  canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoomBy(e.deltaY<0?1.08:1/1.08);},{passive:false});
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',e=>{this.reduced=e.matches;if(e.matches)this.paused=true;});
  this.last=0;this.tick=this.tick.bind(this);requestAnimationFrame(this.tick);
 }
 resize(){const r=this.canvas.getBoundingClientRect();if(!r.width||!r.height)return;this.width=r.width;this.height=r.height;const dpr=Math.min(devicePixelRatio||1,2);this.canvas.width=Math.round(r.width*dpr);this.canvas.height=Math.round(r.height*dpr);this.ctx.setTransform(dpr,0,0,dpr,0,0);this.layout();this.draw(performance.now());}
 layout(){const old=new Map(this.nodes.map(n=>[n.id,n]));const l=layoutByKind(this.data.memories,this.width||700,this.height||540);this.groups=l.groups;this.nodes=l.nodes.map(m=>({...m,shownDetail:old.get(m.id)?.shownDetail??m.detail,shownGist:old.get(m.id)?.shownGist??m.gist_retention}));}
 setData(data,selected,kinds){
  // Scope switches must erase the previous private view immediately.
  if(this.data.scope!==data.scope){this.nodes=[];this.panX=this.panY=0;this.tooltip&&(this.tooltip.hidden=true);}
  this.data=data;this.selected=selected;this.visibleKinds=kinds;this.layout();this.canvas.dataset.layout='kind-islands';this.draw(performance.now());
 }
 zoomBy(n){this.zoom=Math.max(.65,Math.min(2.8,this.zoom*n));this.draw(performance.now());}
 recenter(){this.zoom=1;this.panX=this.panY=0;this.draw(performance.now());}
 hit(e){const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;return [...this.positions].reverse().find(p=>Math.hypot(p.x-x,p.y-y)<Math.max(12,p.r+7));}
 move(e){if(this.drag){const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;this.drag.moved+=Math.abs(dx)+Math.abs(dy);this.panX+=dx;this.panY+=dy;this.drag.x=e.clientX;this.drag.y=e.clientY;if(this.tooltip)this.tooltip.hidden=true;this.draw(performance.now());return;}
  const p=this.hit(e);this.hover=p?.id||null;this.canvas.style.cursor=p?'pointer':'grab';if(this.tooltip){this.tooltip.hidden=!p;if(p){this.tooltip.textContent=p.title;this.tooltip.style.left=Math.max(8,Math.min(this.width-200,p.x+14))+'px';this.tooltip.style.top=Math.max(12,p.y-35)+'px';}}}
 tick(now){const dt=Math.min(now-(this.last||now),50);this.last=now;if(!document.hidden&&this.width&&this.canvas.getBoundingClientRect().height){
  const a=this.reduced?1:1-Math.exp(-dt/180);let changing=false;for(const n of this.nodes){n.shownDetail+=(n.detail-n.shownDetail)*a;n.shownGist+=(n.gist_retention-n.shownGist)*a;if(Math.abs(n.shownDetail-n.detail)>.002)changing=true;}
  this.canvas.dataset.transitioning=String(changing);this.draw(now);
 }requestAnimationFrame(this.tick);}
 point(x,y){return{x:(x-this.width/2)*this.zoom+this.width/2+this.panX,y:(y-this.height/2)*this.zoom+this.height/2+this.panY};}
 shape(p,color,alpha){const c=this.ctx,r=p.r;c.fillStyle=rgba(color,alpha);c.beginPath();
  if(p.kind==='knowledge'){c.moveTo(p.x,p.y-r*1.3);c.lineTo(p.x+r*1.1,p.y);c.lineTo(p.x,p.y+r*1.3);c.lineTo(p.x-r*1.1,p.y);}
  else if(p.kind==='method'||p.kind==='relation'){const count=p.kind==='method'?6:5;for(let i=0;i<count*2;i++){const rr=i%2?r*.7:r*1.25,a=i*Math.PI/count-Math.PI/2;c.lineTo(p.x+Math.cos(a)*rr,p.y+Math.sin(a)*rr);}}
  else if(p.kind==='document')c.roundRect(p.x-r,p.y-r*1.15,r*2,r*2.3,2);
  else if(p.kind==='plan'){c.moveTo(p.x,p.y-r*1.3);c.lineTo(p.x+r*1.2,p.y+r);c.lineTo(p.x-r*1.2,p.y+r);}
  else if(p.kind==='preference'){c.moveTo(p.x,p.y+r);c.bezierCurveTo(p.x-r*2,p.y-r*.2,p.x-r*.8,p.y-r*1.7,p.x,p.y-r*.6);c.bezierCurveTo(p.x+r*.8,p.y-r*1.7,p.x+r*2,p.y-r*.2,p.x,p.y+r);}
  else c.arc(p.x,p.y,r,0,TAU);c.closePath();c.fill();
 }
 draw(now){
  const c=this.ctx,w=this.width,h=this.height;if(!w||!h)return;c.clearRect(0,0,w,h);
  for(const g of this.groups){if(!this.visibleKinds.has(g.kind))continue;const p=this.point(g.x+5,g.y+5),gw=(g.w-10)*this.zoom,gh=(g.h-10)*this.zoom;
   c.fillStyle=rgba(COLORS[g.kind],.045);c.strokeStyle=rgba(COLORS[g.kind],.17);c.lineWidth=1;c.setLineDash([3,7]);c.beginPath();c.roundRect(p.x,p.y,gw,gh,18);c.fill();c.stroke();c.setLineDash([]);c.font='11px "Microsoft YaHei UI",sans-serif';c.fillStyle=rgba(COLORS[g.kind],.95);c.fillText(NAMES[g.kind]+' · '+this.nodes.filter(n=>n.kind===g.kind).length,p.x+14,p.y+22);
  }
  this.positions=this.nodes.filter(n=>this.visibleKinds.has(n.kind)).map((n,i)=>{const p=this.point(n.px,n.py);return {...n,...p,y:p.y+(!this.twoD&&!this.paused&&!this.reduced?Math.sin(now*.0003+i)*1.3:0),r:(3.3+n.importance*3)*Math.min(this.zoom,1.5)};});
  const byId=new Map(this.positions.map(p=>[p.id,p])),active=new Set(this.data.trace.map(t=>t.id)),near=new Set([this.selected]),edges=this.data.edges.filter(e=>byId.has(e.source)&&byId.has(e.target));
  for(const e of edges)if(e.source===this.selected||e.target===this.selected){near.add(e.source);near.add(e.target);}
  const selectedEdges=edges.filter(e=>e.source===this.selected||e.target===this.selected);this.canvas.dataset.highlightedEdges=String(selectedEdges.length);
  const summary=document.querySelector('#selection-summary');if(summary)summary.textContent=this.selected?'已点亮 '+selectedEdges.length+' 条直接关联 · 点击连线两端继续探索':'选择一段记忆，看看它与谁相连';
  const path=(a,b)=>{c.beginPath();c.moveTo(a.x,a.y);c.quadraticCurveTo((a.x+b.x)/2+(b.y-a.y)*.04,(a.y+b.y)/2+(a.x-b.x)*.04,b.x,b.y);};
  for(const e of [...edges.filter(e=>!selectedEdges.includes(e)),...selectedEdges]){const a=byId.get(e.source),b=byId.get(e.target),sel=selectedEdges.includes(e),rec=this.data.trace.some(t=>t.via&&(t.via===a.id&&t.id===b.id||t.via===b.id&&t.id===a.id));c.setLineDash(e.inferred?[4,5]:[]);
   if(sel){path(a,b);c.strokeStyle='rgba(128,170,233,.24)';c.lineWidth=7;c.stroke();}
   path(a,b);c.strokeStyle=sel?'#567cc6':rec?'#7c9aba':rgba('#8fa5bf',this.selected ? .065 : .22);c.lineWidth=sel?2.2:rec?1.4:.8;c.stroke();
  }c.setLineDash([]);
  for(const p of this.positions){const isSelected=p.id===this.selected,related=near.has(p.id),recalled=active.has(p.id),color=COLORS[p.kind],fade=this.selected&&!related&&!recalled ? .38 : 1,alpha=(.2+p.shownDetail*.8)*fade;
   if(isSelected||related||recalled){this.glow(p.x,p.y,p.r*5,color,isSelected ? .25 : .13);c.beginPath();c.arc(p.x,p.y,p.r+5,0,TAU);c.strokeStyle=rgba(color,isSelected ? .9 : .6);c.lineWidth=isSelected?1.8:1.2;c.stroke();}
   if(p.protected){c.beginPath();c.arc(p.x,p.y,p.r+9,0,TAU);c.strokeStyle=rgba('#c39b58',.75);c.lineWidth=1;c.stroke();}
   this.shape(p,color,alpha);
  }
  const boxes=[];let printed=0;const labels=[...this.positions].sort((a,b)=>((b.id===this.selected?10:0)+(near.has(b.id)?5:0)+(active.has(b.id)?3:0)+b.importance)-((a.id===this.selected?10:0)+(near.has(a.id)?5:0)+(active.has(a.id)?3:0)+a.importance));
  for(const p of labels){const important=near.has(p.id)||active.has(p.id);if(printed>Math.min(22,Math.round(w/38))&&!important)continue;if(!important&&p.importance<.7)continue;c.font=(p.id===this.selected?'bold ':'')+'10px "Microsoft YaHei UI",sans-serif';const title=p.title.length>13?p.title.slice(0,12)+'…':p.title,tw=c.measureText(title).width;let x=p.x+p.r+8,y=p.y-5;if(x+tw>w-14)x=p.x-p.r-tw-8;
   const box={x:x-3,y:y-11,w:tw+6,h:17};if(x<7||y<95||y>h-42||boxes.some(b=>box.x<b.x+b.w&&box.x+box.w>b.x&&box.y<b.y+b.h&&box.y+box.h>b.y))continue;boxes.push(box);printed++;c.fillStyle='rgba(250,252,255,.9)';c.fillRect(box.x,box.y,box.w,box.h);c.fillStyle=p.id===this.selected?'#394e85':important?'#5c729f':rgba('#76849e',this.selected ? .38 : .85);c.fillText(title,x,y+2);
  }
 }
 glow(x,y,r,color,a){const c=this.ctx,g=c.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,rgba(color,a));g.addColorStop(1,rgba(color,0));c.fillStyle=g;c.beginPath();c.arc(x,y,r,0,TAU);c.fill();}
}
