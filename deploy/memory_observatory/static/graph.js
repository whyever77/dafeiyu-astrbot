// Starfield layout in display space. Positions are layout, never semantic embeddings.
export const COLORS={episode:'#7fb0d8',preference:'#d6bd8a',knowledge:'#8f9fe0',method:'#a78fce',document:'#86a9c4',plan:'#d39a86',relation:'#b48fbf',unclassified:'#8c9ab5'};
export const NAMES={episode:'经历',preference:'偏好',knowledge:'知识',method:'方法',document:'文档',plan:'计划',relation:'关系',unclassified:'未分类'};
const TAU=Math.PI*2,ORDER=Object.keys(COLORS);
const SPACE={top:'#040817',deep:'#081530',star:'#cfe3ff',text:'#d7e4f7',muted:'#8ea4c7',
  nebula:['#2b3f7d','#4a3a73','#1d5a6b','#6b512f','#263a6b']};
const ANCHORS={
  episode:{center:[-.22,-.02,.05],radius:.30},
  knowledge:{center:[.52,.26,-.10],radius:.22},
  preference:{center:[-.60,-.30,.18],radius:.18},
  method:{center:[-.58,.36,-.32],radius:.15},
  document:{center:[.56,-.14,.26],radius:.14},
  plan:{center:[.02,-.50,-.12],radius:.14},
  relation:{center:[.05,.52,-.38],radius:.12},
  unclassified:{center:[.50,-.46,-.30],radius:.10}
};
function hash(s){let n=2166136261;for(const c of String(s))n=Math.imul(n^c.charCodeAt(0),16777619);return n>>>0;}
function rng(seed){let a=seed>>>0;return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
function rgba(hex,a){return 'rgba('+parseInt(hex.slice(1,3),16)+','+parseInt(hex.slice(3,5),16)+','+parseInt(hex.slice(5,7),16)+','+Math.max(0,Math.min(1,a))+')';}
function clamp(v,a,b){return Math.max(a,Math.min(b,v));}
function ease(t){return t*t*(3-2*t);}

export function layoutStarfields(memories){
 const counts={};
 for(const memory of memories){const kind=COLORS[memory.kind]?memory.kind:'unclassified';counts[kind]=(counts[kind]||0)+1;}
 const groups=ORDER.map(kind=>({kind,center:[...ANCHORS[kind].center],radius:ANCHORS[kind].radius,count:counts[kind]||0}));
 const nodes=[];
 for(const memory of memories){
  const kind=COLORS[memory.kind]?memory.kind:'unclassified',anchor=ANCHORS[kind],r=rng(hash(memory.id));
  const radius=anchor.radius*Math.pow(r(),.55),angle=r()*TAU,outlier=hash('o'+memory.id)%17===0?1.22:1;
  nodes.push({...memory,kind,x:anchor.center[0]+Math.cos(angle)*radius*outlier,
   y:anchor.center[1]+(r()-.5)*anchor.radius*.82,
   z:anchor.center[2]+Math.sin(angle)*radius*.95*outlier});
 }
 return {groups,nodes};
}

export function toggleSelection(selected,id){return selected===id?null:id;}

function makeStars(){
 const r=rng(20261030),stars=[];
 for(let i=0;i<420;i++)stars.push({x:r(),y:r(),size:.35+r()*1.35,alpha:.12+r()*.52,layer:.2+r()*.8});
 return stars;
}

export class Constellation{
 constructor(canvas,onSelect){
  this.canvas=canvas;this.ctx=canvas.getContext('2d');this.onSelect=onSelect;
  this.data={memories:[],edges:[],trace:[],scope:null};this.nodes=[];this.groups=[];this.positions=[];
  this.selected=null;this.visibleKinds=new Set(ORDER);this.twoD=false;this.focusedKind=null;
  this.camera={yaw:.22,pitch:.13,distance:2.35,target:[-.02,0,0]};this.cameraTween=null;
  this.flat={scale:1,panX:0,panY:0};this.flatBase=520;this.focal=900;
  this.width=0;this.height=0;this.time=0;this.last=0;this.drag=null;this.hover=null;this.categoryHits=[];this.relatedIds=new Set();
  this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;this.paused=this.reduced;
  this.stars=makeStars();this.tooltip=document.querySelector('#graph-tooltip');
  // A small debug/test handle; the page already holds this in-memory view.
  this.canvas.__constellation=this;
  new ResizeObserver(()=>this.resize()).observe(canvas);
  canvas.addEventListener('pointerdown',e=>{this.drag={x:e.clientX,y:e.clientY,moved:0};try{canvas.setPointerCapture(e.pointerId);}catch{}});
  canvas.addEventListener('pointermove',e=>this.move(e));
  canvas.addEventListener('pointerup',e=>this.up(e));
  canvas.addEventListener('pointercancel',()=>{this.drag=null;});
  canvas.addEventListener('pointerleave',()=>{this.hover=null;if(this.tooltip)this.tooltip.hidden=true;});
  canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoomBy(e.deltaY<0?1.1:1/1.1);},{passive:false});
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',e=>{this.reduced=e.matches;if(e.matches)this.paused=true;});
  this.tick=this.tick.bind(this);requestAnimationFrame(this.tick);
 }

 resize(){
  const rect=this.canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;
  this.width=rect.width;this.height=rect.height;
  const dpr=Math.min(devicePixelRatio||1,2);
  this.canvas.width=Math.round(rect.width*dpr);this.canvas.height=Math.round(rect.height*dpr);
  this.ctx.setTransform(dpr,0,0,dpr,0,0);
  this.focal=clamp(this.height*1.9,480,this.width*1.1);
  this.flatBase=Math.min(this.width,this.height)*.95;
  this.draw(performance.now());
 }

 setData(data,selected,kinds){
  const scopeChanged=this.data.scope!==data.scope;
  const old=new Map(this.nodes.map(node=>[node.id,node]));
  this.data=data;this.selected=selected;this.visibleKinds=kinds;
  const layout=layoutStarfields(data.memories);
  this.groups=layout.groups;
  this.nodes=layout.nodes.map(node=>{const previous=old.get(node.id);return {...node,
   shownDetail:previous?previous.shownDetail:(node.detail??1),shownGist:previous?previous.shownGist:(node.gist_retention??1)};});
  if(scopeChanged)this.resetCamera(true);
  this.canvas.dataset.layout='starfields';
  this.draw(performance.now());
 }

 clearScope(){
  this.nodes=[];this.groups=[];this.positions=[];this.selected=null;this.categoryHits=[];this.relatedIds=new Set();
  this.resetCamera(true);this.canvas.dataset.layout='starfields';
 }

 previewDays(days){
  // Track the slider locally while the debounced server projection is in flight;
  // the server response replaces these targets with the same formula.
  const asOf=Date.parse(this.data.as_of||'');if(!Number.isFinite(asOf))return;
  const at=asOf+Number(days)*86400000;
  for(const node of this.nodes){
   if(node.protected){node.detail=1;node.gist_retention=1;continue;}
   const recalled=Date.parse(node.last_recalled_at||node.created_at||'');if(!Number.isFinite(recalled))continue;
   const age=Math.max(0,(at-recalled)/86400000);
   const stability=Math.max(1,(node.half_life_days||30)*(node.simulated_rehearsal?1.6:1));
   node.detail=Math.pow(2,-age/stability);
   node.gist_retention=Math.pow(2,-age/(stability*3));
  }
 }

 projectPoint(x,y,z){
  if(this.twoD){
   const base=this.flatBase||520,scale=base*this.flat.scale;
   return {x:this.width/2+x*scale+this.flat.panX,y:this.height/2-y*scale+this.flat.panY,
    s:scale/base,zc:1.5+z};
  }
  const cam=this.camera,x0=x-cam.target[0],y0=y-cam.target[1],z0=z-cam.target[2];
  const cy=Math.cos(cam.yaw),sy=Math.sin(cam.yaw);
  const X=x0*cy-z0*sy,Z=x0*sy+z0*cy;
  const cp=Math.cos(cam.pitch),sp=Math.sin(cam.pitch);
  const Y=y0*cp-Z*sp,Z2=y0*sp+Z*cp,zc=Z2+cam.distance;
  if(zc<.18)return null;
  const s=this.focal/zc;
  return {x:this.width/2+X*s,y:this.height/2-Y*s,s,zc};
 }

 projectNode(node){
  const p=this.projectPoint(node.x,node.y,node.z);if(!p)return null;
  if(!this.twoD&&!this.paused&&!this.reduced)p.y+=Math.sin(this.time/1500+(hash(node.id)%100))*.8;
  return p;
 }

 zoomBy(factor){
  if(this.twoD)this.flat.scale=clamp(this.flat.scale*factor,.65,6);
  else{this.camera.distance=clamp(this.camera.distance/factor,.65,5);this.cameraTween=null;}
  this.draw(performance.now());
 }

 recenter(){this.focusedKind=null;this.resetCamera(false);}

 resetCamera(instant=false){
  this.focusedKind=null;this.cameraTween=null;
  if(this.twoD){this.flat={scale:1,panX:0,panY:0};this.draw(performance.now());return;}
  const to={yaw:.22,pitch:.13,distance:2.35,target:[0,0,0]};
  if(instant||this.reduced){Object.assign(this.camera,to);this.draw(performance.now());return;}
  this.tweenCamera(to,700);this.draw(performance.now());
 }

 tweenCamera(to,duration=800){
  if(this.reduced){Object.assign(this.camera,to);this.cameraTween=null;return;}
  this.cameraTween={from:{yaw:this.camera.yaw,pitch:this.camera.pitch,distance:this.camera.distance,target:[...this.camera.target]},
   to:{yaw:to.yaw??this.camera.yaw,pitch:to.pitch??this.camera.pitch,distance:to.distance??this.camera.distance,target:[...(to.target||this.camera.target)]},
   start:performance.now(),duration};
 }

 updateTween(now){
  if(!this.cameraTween)return;
  const tween=this.cameraTween,t=clamp((now-tween.start)/tween.duration,0,1),u=ease(t);
  this.camera.yaw=tween.from.yaw+(tween.to.yaw-tween.from.yaw)*u;
  this.camera.pitch=tween.from.pitch+(tween.to.pitch-tween.from.pitch)*u;
  this.camera.distance=tween.from.distance+(tween.to.distance-tween.from.distance)*u;
  this.camera.target=tween.from.target.map((v,i)=>v+(tween.to.target[i]-v)*u);
  if(t>=1)this.cameraTween=null;
 }

 focusKind(kind){
  if(!this.visibleKinds.has(kind))return;
  const group=this.groups.find(g=>g.kind===kind);if(!group||!group.count)return;
  if(this.focusedKind===kind){this.recenter();return;}
  this.focusedKind=kind;
  if(this.twoD){
   const scale=clamp(1/(group.radius*1.6),1.2,5.5),base=this.flatBase||520;
   this.flat={scale,panX:-group.center[0]*base*scale,panY:group.center[1]*base*scale};
   this.draw(performance.now());return;
  }
  this.tweenCamera({target:[...group.center],distance:clamp(group.radius*2.4,.55,2.4)},900);
  this.draw(performance.now());
 }

 drawBackground(){
  const c=this.ctx,W=this.width,H=this.height;
  const g=c.createRadialGradient(W*.52,H*.44,0,W*.52,H*.48,H*.92);
  g.addColorStop(0,SPACE.deep);g.addColorStop(.6,SPACE.top);g.addColorStop(1,'#02040b');
  c.fillStyle=g;c.fillRect(0,0,W,H);
  const ox=this.twoD?this.flat.panX*.05:Math.sin(this.camera.yaw)*26;
  const oy=this.twoD?this.flat.panY*.05:Math.sin(this.camera.pitch)*18;
  c.save();c.globalCompositeOperation='screen';
  const blobs=[[.17,.30,.46,.62],[.76,.22,.40,.50],[.65,.74,.46,.55],[.28,.80,.33,.40],[.86,.56,.30,.34],[.45,.50,.55,.30]];
  blobs.forEach(([bx,by,br,ba],i)=>{const color=SPACE.nebula[i%SPACE.nebula.length];
   const cx=bx*W+ox*.25,cy=by*H+oy*.25,rad=Math.max(W,H)*br;
   const ng=c.createRadialGradient(cx,cy,0,cx,cy,rad);
   ng.addColorStop(0,rgba(color,.24*ba*2));ng.addColorStop(.45,rgba(color,.11*ba*2));ng.addColorStop(1,rgba(color,0));
   c.fillStyle=ng;c.fillRect(cx-rad,cy-rad,rad*2,rad*2);});
  c.restore();
  c.save();c.translate(W*.5,H*.5);c.rotate(-.35);
  const band=c.createLinearGradient(0,-H*.22,0,H*.22);
  band.addColorStop(0,rgba(SPACE.star,0));band.addColorStop(.5,rgba(SPACE.star,.035));band.addColorStop(1,rgba(SPACE.star,0));
  c.fillStyle=band;c.fillRect(-W,-H*.22,W*2,H*.44);c.restore();
  for(const star of this.stars){const x=(star.x*W+ox*star.layer+W)%W,y=(star.y*H+oy*star.layer+H)%H;
   c.fillStyle=rgba(SPACE.star,star.alpha);c.beginPath();c.arc(x,y,star.size,0,TAU);c.fill();}
  const v=c.createRadialGradient(W*.5,H*.48,H*.22,W*.5,H*.5,H*.86);
  v.addColorStop(0,'rgba(0,0,0,0)');v.addColorStop(.72,'rgba(2,5,14,.18)');v.addColorStop(1,'rgba(1,3,10,.58)');
  c.fillStyle=v;c.fillRect(0,0,W,H);
 }

 drawHaze(){
  for(const group of this.groups){
   if(!this.visibleKinds.has(group.kind)||!group.count)continue;
   const p=this.projectPoint(group.center[0],group.center[1],group.center[2]);if(!p)continue;
   const rad=this.twoD?Math.max(36,group.radius*(this.flatBase||520)*this.flat.scale*2.6):Math.max(36,group.radius*p.s*2.6);
   const color=COLORS[group.kind],g=this.ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,rad);
   g.addColorStop(0,rgba(color,.10));g.addColorStop(.5,rgba(color,.045));g.addColorStop(1,rgba(color,0));
   this.ctx.fillStyle=g;this.ctx.beginPath();this.ctx.arc(p.x,p.y,rad,0,TAU);this.ctx.fill();
  }
 }

 drawEdges(placed){
  const c=this.ctx,byId=new Map(placed.map(p=>[p.id,p])),edges=[];
  for(const edge of this.data.edges){const a=byId.get(edge.source),b=byId.get(edge.target);if(a&&b)edges.push({edge,a,b,depth:(a.zc+b.zc)/2});}
  edges.sort((a,b)=>b.depth-a.depth);
  const selectedEdges=edges.filter(e=>this.selected&&(e.edge.source===this.selected||e.edge.target===this.selected));
  this.canvas.dataset.highlightedEdges=String(selectedEdges.length);
  const summary=document.querySelector('#selection-summary');
  if(summary)summary.textContent=this.selected?'已点亮 '+selectedEdges.length+' 条直接关联 · 再次点击同一节点取消':'选择一段记忆，点亮它的关联；再次点击同一节点取消';
  for(const {edge,a,b} of edges){
   const selected=this.selected&&(edge.source===this.selected||edge.target===this.selected);
   const cross=a.kind!==b.kind,fade=clamp(1-((a.zc+b.zc)/2-1.65)/2.2*.5,.45,1);
   const alpha=selected?.50:cross?.10:.045,c1=COLORS[a.kind],c2=COLORS[b.kind];
   const grad=c.createLinearGradient(a.x,a.y,b.x,b.y);
   grad.addColorStop(0,rgba(c1,alpha*fade));grad.addColorStop(1,rgba(c2,alpha*fade));
   const bend=cross?.20:.045,mx=(a.x+b.x)/2-(b.y-a.y)*bend,my=(a.y+b.y)/2+(b.x-a.x)*bend;
   if(selected){c.strokeStyle=rgba('#a9c7ff',.14);c.lineWidth=6;c.setLineDash([]);c.beginPath();c.moveTo(a.x,a.y);c.quadraticCurveTo(mx,my,b.x,b.y);c.stroke();}
   c.strokeStyle=grad;c.lineWidth=selected?1.9:cross?.8:.5;
   c.setLineDash(edge.inferred?[3,6]:[]);c.beginPath();c.moveTo(a.x,a.y);c.quadraticCurveTo(mx,my,b.x,b.y);c.stroke();
  }
  c.setLineDash([]);
 }

 drawNodes(placed){
  const c=this.ctx,list=[...placed].sort((a,b)=>b.zc-a.zc);
  for(const p of list){
   const color=COLORS[p.kind]||COLORS.unclassified;
   const scale=this.twoD?p.s:p.s/700;
   const r=(this.twoD?2.4+p.importance*3.2:2.2+p.importance*3.6)*clamp(scale,.55,1.45);
   p.r=r;
   const fade=this.twoD?1:clamp(1-(p.zc-1.65)/2.2*.55,.42,1);
   const isSelected=p.id===this.selected,isRelated=this.relatedIds.has(p.id);
   const emphasis=isSelected||isRelated?1:(this.selected?.72:.92);
   const alpha=clamp((.20+.80*p.shownDetail)*fade*emphasis,.12,1);
   const halo=r*(2.0+3.8*p.shownDetail);
   const glow=c.createRadialGradient(p.x,p.y,0,p.x,p.y,halo);
   glow.addColorStop(0,rgba(color,.24*p.shownDetail*fade));glow.addColorStop(.55,rgba(color,.08*p.shownDetail*fade));glow.addColorStop(1,rgba(color,0));
   c.fillStyle=glow;c.beginPath();c.arc(p.x,p.y,halo,0,TAU);c.fill();
   this.shape(p,r,color,alpha);
   if(isRelated&&!isSelected){c.strokeStyle=rgba(color,.72);c.lineWidth=1.3;c.beginPath();c.arc(p.x,p.y,r+5,0,TAU);c.stroke();}
   if(p.protected){c.strokeStyle=rgba('#d6bd8a',.58);c.lineWidth=1;c.beginPath();c.arc(p.x,p.y,r+4,0,TAU);c.stroke();}
  }
  const selected=placed.find(p=>p.id===this.selected);
  if(selected){const r=selected.r||4;c.strokeStyle='rgba(220,236,255,.7)';c.lineWidth=1.2;c.beginPath();c.arc(selected.x,selected.y,r+6,0,TAU);c.stroke();
   c.strokeStyle=rgba(COLORS[selected.kind],.35);c.lineWidth=1;c.beginPath();c.arc(selected.x,selected.y,r+11,0,TAU);c.stroke();}
 }

 shape(p,r,color,alpha){
  const c=this.ctx;c.fillStyle=rgba(color,alpha);c.beginPath();
  if(p.kind==='knowledge'){c.moveTo(p.x,p.y-r*1.35);c.lineTo(p.x+r*1.15,p.y);c.lineTo(p.x,p.y+r*1.35);c.lineTo(p.x-r*1.15,p.y);}
  else if(p.kind==='method'||p.kind==='relation'){const count=p.kind==='method'?6:5;for(let i=0;i<count*2;i++){const rr=i%2?r*.68:r*1.28,a=i*Math.PI/count-Math.PI/2;c.lineTo(p.x+Math.cos(a)*rr,p.y+Math.sin(a)*rr);}}
  else if(p.kind==='document')c.roundRect(p.x-r,p.y-r*1.15,r*2,r*2.3,2);
  else if(p.kind==='plan'){c.moveTo(p.x,p.y-r*1.35);c.lineTo(p.x+r*1.2,p.y+r);c.lineTo(p.x-r*1.2,p.y+r);}
  else if(p.kind==='preference'){c.moveTo(p.x,p.y+r);c.bezierCurveTo(p.x-r*2,p.y-r*.2,p.x-r*.8,p.y-r*1.7,p.x,p.y-r*.6);c.bezierCurveTo(p.x+r*.8,p.y-r*1.7,p.x+r*2,p.y-r*.2,p.x,p.y+r);}
  else c.arc(p.x,p.y,r,0,TAU);
  c.closePath();c.fill();
 }

 labelBox(text,x,y,opts={}){
  const c=this.ctx,font=opts.font||'11px "Microsoft YaHei UI",sans-serif',pad=opts.pad??7,height=opts.height??22,dot=Boolean(opts.dot);
  c.font=font;const tw=c.measureText(text).width,w=tw+pad*2+(dot?12:0),h=height,avoid=opts.avoid||[];
  const candidates=[[x,y],[x-w,y],[x,y-height],[x-w,y-height],[x+10,y+20],[x-w,y+20]];
  for(const [rawX,rawY] of candidates){
   const bx=clamp(rawX,10,Math.max(10,this.width-10-w)),by=clamp(rawY,96,Math.max(96,this.height-66-h));
   const box={x:bx,y:by,w,h};
   if(avoid.some(b=>bx<b.x+b.w&&bx+w>b.x&&by<b.y+b.h&&by+h>b.y))continue;
   c.fillStyle=opts.bg||'rgba(3,8,20,.55)';c.strokeStyle=opts.border||'rgba(190,215,250,.12)';c.lineWidth=1;
   c.beginPath();c.roundRect(bx,by,w,h,9);c.fill();c.stroke();
   let tx=bx+pad;
   if(dot){c.fillStyle=opts.color||SPACE.star;c.beginPath();c.arc(bx+pad+3,by+h/2,2.6,0,TAU);c.fill();tx=bx+pad+12;}
   c.fillStyle=opts.textColor||SPACE.text;c.font=font;c.textBaseline='middle';c.fillText(text,tx,by+h/2+.5);c.textBaseline='alphabetic';
   return box;
  }
  return null;
 }

 drawLabels(placed){
  const boxes=[];this.categoryHits=[];const byId=new Map(placed.map(p=>[p.id,p]));
  for(const group of this.groups){
   if(!this.visibleKinds.has(group.kind)||!group.count)continue;
   const points=placed.filter(p=>p.kind===group.kind);if(!points.length)continue;
   const cx=points.reduce((sum,p)=>sum+p.x,0)/points.length,top=Math.min(...points.map(p=>p.y)),color=COLORS[group.kind];
   const box=this.labelBox(NAMES[group.kind]+' · '+group.count,cx-8,Math.max(96,top-40),{font:'600 13px "Microsoft YaHei UI",sans-serif',
    height:26,pad:9,dot:true,color,avoid:boxes,bg:'rgba(3,8,20,.42)',border:rgba(color,.18),textColor:'#e6efff'});
   if(box){boxes.push(box);this.categoryHits.push({kind:group.kind,...box});}
  }
  const ids=[],seen=new Set();
  if(this.selected){ids.push(this.selected);seen.add(this.selected);}
  const related=[...this.relatedIds].map(id=>byId.get(id)).filter(Boolean).sort((a,b)=>b.importance-a.importance).slice(0,3);
  for(const p of related){if(ids.length>=4)break;if(!seen.has(p.id)){ids.push(p.id);seen.add(p.id);}}
  for(const trace of this.data.trace||[]){if(ids.length>=4)break;if(!seen.has(trace.id)){ids.push(trace.id);seen.add(trace.id);}}
  for(const id of ids){
   const p=byId.get(id);if(!p)continue;
   const selected=id===this.selected,title=String(p.title||'');
   const text=title.length>13?title.slice(0,12)+'…':title;
   const box=this.labelBox(text,p.x+12,p.y-12,{font:(selected?'600 ':'')+'11px "Microsoft YaHei UI",sans-serif',
    height:22,pad:7,avoid:boxes,bg:'rgba(3,8,20,.52)',border:selected?rgba(COLORS[p.kind],.30):'rgba(190,215,250,.08)',
    textColor:selected?'#f0f6ff':SPACE.muted});
   if(box)boxes.push(box);
  }
 }

 draw(now){
  now=now??performance.now();
  if(!this.width||!this.height)return;
  this.time=now;this.updateTween(now);
  const c=this.ctx;c.clearRect(0,0,this.width,this.height);
  this.drawBackground();
  const placed=[];
  for(const node of this.nodes){if(!this.visibleKinds.has(node.kind))continue;const p=this.projectNode(node);if(p)placed.push({...node,...p});}
  this.positions=placed;
  this.relatedIds=new Set();
  if(this.selected)for(const edge of this.data.edges){
   if(edge.source===this.selected)this.relatedIds.add(edge.target);
   else if(edge.target===this.selected)this.relatedIds.add(edge.source);
  }
  this.canvas.dataset.relatedNodes=String(this.relatedIds.size);
  this.drawHaze();
  this.drawEdges(placed);
  this.drawNodes(placed);
  this.drawLabels(placed);
  this.canvas.dataset.layout='starfields';
 }

 hit(e){
  const rect=this.canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
  const hits=this.positions.filter(p=>Math.hypot(p.x-x,p.y-y)<=Math.max(11,(p.r||4)+7));
  hits.sort((a,b)=>a.zc-b.zc);
  return hits[0]||null;
 }

 categoryAt(e){
  const rect=this.canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
  return this.categoryHits.find(b=>x>=b.x&&x<=b.x+b.w&&y>=b.y&&y<=b.y+b.h)||null;
 }

 move(e){
  if(this.drag){
   const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;this.drag.moved+=Math.abs(dx)+Math.abs(dy);this.drag.x=e.clientX;this.drag.y=e.clientY;
   if(this.tooltip)this.tooltip.hidden=true;
   if(this.twoD){this.flat.panX+=dx;this.flat.panY+=dy;}
   else{this.camera.yaw+=dx*.0035;this.camera.pitch=clamp(this.camera.pitch+dy*.0025,-.55,.55);this.cameraTween=null;}
   this.draw(performance.now());return;
  }
  const category=this.categoryAt(e),p=category?null:this.hit(e);
  this.hover=p?.id||null;this.canvas.style.cursor=category||p?'pointer':'grab';
  if(this.tooltip){
   this.tooltip.hidden=!category&&!p;
   if(category){this.tooltip.textContent='点击靠近星域；再次点击返回全景';this.tooltip.style.left=Math.max(8,Math.min(this.width-220,category.x+category.w/2-60))+'px';this.tooltip.style.top=Math.max(10,category.y-34)+'px';}
   else if(p){this.tooltip.textContent=p.title;this.tooltip.style.left=Math.max(8,Math.min(this.width-200,p.x+14))+'px';this.tooltip.style.top=Math.max(12,p.y-35)+'px';}
  }
 }

 up(e){
  const drag=this.drag;this.drag=null;
  if(!drag||drag.moved>=5)return;
  const category=this.categoryAt(e);if(category){this.focusKind(category.kind);return;}
  const p=this.hit(e);if(p&&this.onSelect)this.onSelect(toggleSelection(this.selected,p.id));
 }

 tick(now){
  const dt=Math.min(now-(this.last||now),50);this.last=now;
  if(!document.hidden&&this.width&&this.canvas.getBoundingClientRect().height){
   const a=this.reduced?1:1-Math.exp(-dt/120);let changing=false;
   for(const node of this.nodes){
    const target=node.detail??1,gist=node.gist_retention??1;
    node.shownDetail+=(target-node.shownDetail)*a;node.shownGist+=(gist-node.shownGist)*a;
    if(Math.abs(node.shownDetail-target)>.002)changing=true;
   }
   this.canvas.dataset.transitioning=String(changing);
   this.draw(now);
  }
  requestAnimationFrame(this.tick);
 }
}
