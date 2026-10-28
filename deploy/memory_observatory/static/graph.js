// A stable, decorative projection of the memory graph, not an embedding space.
export const COLORS = {episode: '#8ce0d4', preference: '#dbbd8b', knowledge: '#85a9e5', method: '#b6a0dd'};
const TAU = Math.PI * 2;
function hash(text) { let n = 2166136261; for (const c of text) n = Math.imul(n ^ c.charCodeAt(0), 16777619); return (n >>> 0) / 4294967296; }
function rgba(hex, a) {return `rgba(${parseInt(hex.slice(1,3),16)},${parseInt(hex.slice(3,5),16)},${parseInt(hex.slice(5,7),16)},${Math.max(0,Math.min(1,a))})`;}

export class Constellation {
  constructor(canvas, onSelect) {
    this.canvas = canvas; this.ctx = canvas.getContext('2d'); this.onSelect = onSelect;
    this.data = {memories:[], edges:[], trace:[]}; this.positions = []; this.selected = null;
    this.rotation = -.08; this.tilt = .12; this.zoom = 1; this.twoD = false;
    this.paused = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.visibleKinds = new Set(Object.keys(COLORS)); this.hover = null; this.drag = null;
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(canvas);
    this.tooltip = document.querySelector('#graph-tooltip');
    canvas.addEventListener('pointerdown', e => {this.drag = {x:e.clientX,y:e.clientY,moved:0};canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointermove', e => this.move(e));
    canvas.addEventListener('pointerup', e => {if(this.drag && this.drag.moved < 5){const hit=this.hit(e);if(hit)this.onSelect(hit.id);}this.drag=null;});
    canvas.addEventListener('pointercancel',()=>{this.drag=null;});
    canvas.addEventListener('pointerleave',()=>{this.hover=null;this.tooltip.hidden=true;});
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoomBy(e.deltaY < 0 ? 1.08 : 1/1.08);},{passive:false});
    document.addEventListener('visibilitychange',()=>{this.last=0;});
    this.last=0; this.frame=0; this.tick=this.tick.bind(this); requestAnimationFrame(this.tick);
  }
  resize() {
    const rect=this.canvas.getBoundingClientRect(); this.width=rect.width; this.height=rect.height;
    const dpr=Math.min(devicePixelRatio||1,2);this.canvas.width=Math.round(rect.width*dpr);this.canvas.height=Math.round(rect.height*dpr);this.ctx.setTransform(dpr,0,0,dpr,0,0);
    this.draw(performance.now());
  }
  setData(data, selected, kinds) {
    this.data=data;this.selected=selected;this.visibleKinds=kinds;
    const themes=[...new Set(data.memories.map(m=>m.topics[0]||'未分类'))];
    const anchors=[[.03,-.20],[.48,.02],[-.06,.43],[.42,.42],[-.43,-.02],[-.43,.39],[.13,-.48]];
    this.nodes=data.memories.map((m,index)=>{
      const group=themes.indexOf(m.topics[0]||'未分类');
      const anchor=anchors[group%anchors.length];
      const angle=hash(m.id+'angle')*TAU;
      const radius=.10+hash(m.id+'radius')*.18;
      return {...m,px:anchor[0]+Math.cos(angle)*radius,py:anchor[1]+Math.sin(angle)*radius,pz:(hash(m.id+'depth')-.5)*.5,index};
    });
    this.draw(performance.now());
  }
  zoomBy(factor){this.zoom=Math.max(.65,Math.min(1.9,this.zoom*factor));this.draw(performance.now());}
  recenter(){this.rotation=-.08;this.tilt=.12;this.zoom=1;this.draw(performance.now());}
  hit(e){const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;return [...this.positions].reverse().find(p=>Math.hypot(p.x-x,p.y-y)<Math.max(12,p.r+6));}
  move(e){
    if(this.drag){const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;this.drag.moved+=Math.abs(dx)+Math.abs(dy);this.rotation+=dx*.003;this.tilt=Math.max(-.6,Math.min(.6,this.tilt+dy*.002));this.drag.x=e.clientX;this.drag.y=e.clientY;this.draw(performance.now());this.tooltip.hidden=true;return;}
    const hit=this.hit(e);this.hover=hit?.id||null;this.canvas.style.cursor=hit?'pointer':'grab';
    if(hit){this.tooltip.textContent=hit.title;this.tooltip.style.left=`${Math.min(this.width-200,Math.max(8,hit.x+14))}px`;this.tooltip.style.top=`${Math.max(12,hit.y-35)}px`;this.tooltip.hidden=false;}else this.tooltip.hidden=true;
  }
  tick(now){
    if(!document.hidden && this.width && this.canvas.getBoundingClientRect().height){
      if(!this.paused && !this.drag && !this.hover){const delta=Math.min(now-(this.last||now),50);this.rotation+=delta*.000008;}
      if(now-this.frame>32){this.draw(now);this.frame=now;}
    }
    this.last=now;requestAnimationFrame(this.tick);
  }
  draw(now){
    const c=this.ctx,w=this.width,h=this.height;if(!w||!h)return;c.clearRect(0,0,w,h);
    const narrow=w<500;
    const cx=w*.55,cy=h*(narrow?.56:.48),s=Math.min(w*.73,h*.66)*this.zoom;
    const scaleX=w*.57*this.zoom,scaleY=h*(narrow?.46:.60)*this.zoom;
    // Ambient dust is decorative; actual records are the larger labelled nodes.
    for(let i=0;i<185;i++){
      const x=hash('sx'+i)*w,y=hash('sy'+i)*h,opacity=.08+hash('sa'+i)*.22;
      c.fillStyle=rgba('#9dc2cd',opacity);c.beginPath();c.arc(x,y,.35+hash('sr'+i)*.6,0,TAU);c.fill();
    }
    c.save();c.translate(cx,cy);c.rotate(-.27);
    for(let j=0;j<3;j++){c.beginPath();c.ellipse(0,0,s*(.52+j*.09),s*(.35+j*.045),0,0,TAU);c.strokeStyle=`rgba(105,166,176,${.045-j*.008})`;c.lineWidth=.6;c.setLineDash(j===1?[2,8]:[]);c.stroke();}
    c.restore();c.setLineDash([]);
    const cos=Math.cos(this.rotation),sin=Math.sin(this.rotation),tilt=this.tilt;
    this.positions=(this.nodes||[]).filter(n=>this.visibleKinds.has(n.kind)).map(n=>{
      const x=this.twoD?n.px:n.px*cos+n.pz*sin;
      const z=this.twoD?0:-n.px*sin+n.pz*cos;
      const perspective=1+z*.15;
      return {...n,x:cx+x*scaleX*perspective,y:cy+(n.py+(this.twoD?0:z*tilt))*scaleY,r:(2.5+n.importance*3.3)*perspective,z};
    }).sort((a,b)=>a.z-b.z);
    const byId=new Map(this.positions.map(p=>[p.id,p])),active=new Set(this.data.trace.map(t=>t.id));
    const selectedNeighbours=new Set();
    for(const edge of this.data.edges){if(edge.source===this.selected)selectedNeighbours.add(edge.target);if(edge.target===this.selected)selectedNeighbours.add(edge.source);}
    for(const edge of this.data.edges){
      const a=byId.get(edge.source),b=byId.get(edge.target);if(!a||!b)continue;
      const selected=edge.source===this.selected||edge.target===this.selected;
      const recalling=this.data.trace.some(t=>t.via&&(t.via===a.id&&t.id===b.id||t.via===b.id&&t.id===a.id));
      const alpha=(selected ? .48 : .22)*(Math.max(a.gist_retention,b.gist_retention)*.6+.4);
      const midx=(a.x+b.x)/2+(a.y-b.y)*.05,midy=(a.y+b.y)/2+(b.x-a.x)*.05;
      c.beginPath();c.moveTo(a.x,a.y);c.quadraticCurveTo(midx,midy,b.x,b.y);c.strokeStyle=rgba(recalling?'#b7f1dc':'#7bb9c0',recalling ? .6 : alpha);c.lineWidth=recalling?1.1:.75;c.setLineDash(edge.inferred?[2,5]:[]);c.stroke();
      if(recalling&&!this.paused){const t=(now*.00015+hash(a.id))%1,tx=(1-t)**2*a.x+2*(1-t)*t*midx+t*t*b.x,ty=(1-t)**2*a.y+2*(1-t)*t*midy+t*t*b.y;this.glow(tx,ty,8,'#c1f8df',.4);c.fillStyle='#c1f8df';c.beginPath();c.arc(tx,ty,1.5,0,TAU);c.fill();}
    }
    c.setLineDash([]);
    const labels=[];
    // Reserve the title area, so labels never fight the editorial caption.
    labels.push({x:18,y:72,w:185,h:135});
    for(const p of this.positions){
      const color=COLORS[p.kind],selected=p.id===this.selected,hover=p.id===this.hover,recalled=active.has(p.id);
      const a=.20+p.detail*.8;
      if(p.detail>.08||selected||recalled)this.glow(p.x,p.y,p.r*(selected?10:6),color,(selected?.23:.10)*(.4+p.detail*.6));
      if(selected||recalled||hover){c.beginPath();c.arc(p.x,p.y,p.r+6+(recalled&&!this.paused?Math.sin(now*.0015+p.index)*1.5:0),0,TAU);c.strokeStyle=rgba(color,selected?.6:.35);c.lineWidth=.7;c.stroke();}
      if(selected){c.beginPath();c.arc(p.x,p.y,p.r+14,0,TAU);c.strokeStyle=rgba(color,.15);c.lineWidth=.65;c.stroke();}
      if(p.protected){c.beginPath();c.arc(p.x,p.y,p.r+3,0,TAU);c.strokeStyle=rgba('#e2ce9e',.65);c.lineWidth=.65;c.stroke();}
      c.fillStyle=rgba(color,a);c.beginPath();c.arc(p.x,p.y,p.r,0,TAU);c.fill();
      c.fillStyle=rgba('#e3f8ef',a*.8);c.beginPath();c.arc(p.x-p.r*.17,p.y-p.r*.20,Math.max(.8,p.r*.35),0,TAU);c.fill();
    }
    // Prioritise current selection, matching nodes and informative anchors.
    const labelNodes=[...this.positions].sort((a,b)=>((b.id===this.selected?5:0)+(active.has(b.id)?3:0)+b.importance)-((a.id===this.selected?5:0)+(active.has(a.id)?3:0)+a.importance));
    let printed=0;
    for(const p of labelNodes){
      const selected=p.id===this.selected,recalled=active.has(p.id);
      if(printed>Math.min(13,Math.round(w/55))&&!selected&&!recalled)continue;
      if(p.importance<.65&&!selected&&!recalled)continue;
      c.font=`${selected?11:9}px "Microsoft YaHei UI",sans-serif`;
      const title=p.title.length>14?p.title.slice(0,13)+'…':p.title;
      const width=c.measureText(title).width;let x=p.x+p.r+9,y=p.y-4;
      if(x+width>w-15)x=p.x-p.r-width-9;
      const box={x:x-3,y:y-10,w:width+6,h:18};
      if(x<12||y<24||y>h-42||labels.some(b=>box.x<b.x+b.w&&box.x+box.w>b.x&&box.y<b.y+b.h&&box.y+box.h>b.y))continue;
      labels.push(box);printed++;c.fillStyle=rgba('#071923',.76);c.fillRect(box.x,box.y,box.w,box.h);
      c.fillStyle=selected?'#daf7ed':recalled?'#b9e9e0':rgba('#a4c2cf',.43+p.detail*.4);c.fillText(title,x,y+2);
    }
  }
  glow(x,y,r,color,alpha){const c=this.ctx,g=c.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,rgba(color,alpha));g.addColorStop(.25,rgba(color,alpha*.45));g.addColorStop(1,rgba(color,0));c.fillStyle=g;c.beginPath();c.arc(x,y,r,0,TAU);c.fill();}
}
