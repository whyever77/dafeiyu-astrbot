// Decorative scene only. No source records, timers for bot work, or write APIs.
const $=s=>document.querySelector(s);
export const ZONES={
 overview:{focus:[600,380,1],pose:[700,675,235],action:'idle',label:'在这里安静待一会儿'},
 desk:{focus:[535,455,1.85],pose:[535,640,318],action:'desk',label:'坐下来，写一点今天的安排'},
 shelf:{focus:[250,342,1.85],pose:[300,525,232],action:'shelf',label:'拿起书，再读几页'},
 window:{focus:[787,290,1.9],pose:[810,445,220],action:'window',label:'站在窗边，看看外面的风景'},
 rest:{focus:[1000,475,2.05],pose:[1000,564,260],action:'rest',label:'嘘……让她小睡一会儿'}
};
const ease=t=>t*t*(3-2*t),mix=(a,b,t)=>a+(b-a)*t;
// Sheets are normalized to 444px integer cells with 2px transparent gutters
// (deploy/memory_observatory/tools/normalize_character_atlas.py).
const ATLAS={cols:4,rows:2,cell:444,gap:2,pad:2,width:1786,height:894};
export function dayPhase(date=new Date()){
 const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date);
 const h=Number(parts.find(p=>p.type==='hour').value),m=Number(parts.find(p=>p.type==='minute').value);
 return {hour:h,minute:m,phase:h<5||h>=19?'night':h<8?'dawn':h<17?'day':'sunset',text:`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`};
}
function sceneMarkup(){
 const bookColors=['#7897ca','#d3b0b6','#d8bf8e','#9cabc8','#a8c8bf','#a5b7df'];
 let books='';for(let row=0;row<3;row++)for(let i=0;i<7;i++){const x=120+i*26,y=221+row*64,h=36+(i*7+row*11)%20;books+=`<rect x="${x}" y="${y-h+35}" width="19" height="${h}" rx="2" fill="${bookColors[(row+i)%6]}" stroke="#8799b7"/><path d="M${x+4} ${y+28}h11" stroke="#fff9e8" opacity=".6"/>`;}
 return `<defs>
 <linearGradient id="wall" x2="0" y2="1"><stop stop-color="#fffdf7"/><stop offset="1" stop-color="#ecebfb"/></linearGradient>
 <linearGradient id="floor" x2="0" y2="1"><stop stop-color="#e3d9d2"/><stop offset="1" stop-color="#f7e9da"/></linearGradient>
 <linearGradient id="cabinet" x2="1" y2="1"><stop stop-color="#fefcf7"/><stop offset="1" stop-color="#d5def5"/></linearGradient>
 <linearGradient id="cushion" x2="0" y2="1"><stop stop-color="#dae4ff"/><stop offset="1" stop-color="#b1c4ed"/></linearGradient>
 <linearGradient id="sky" x2="0" y2="1"><stop id="sky-top" stop-color="#8bb9f0"/><stop id="sky-bottom" offset="1" stop-color="#e3efff"/></linearGradient>
 <radialGradient id="lamplight"><stop stop-color="#ffe4a4" stop-opacity=".6"/><stop offset="1" stop-color="#ffe4a4" stop-opacity="0"/></radialGradient>
 <pattern id="dots" width="46" height="46" patternUnits="userSpaceOnUse"><circle cx="23" cy="23" r="1.5" fill="#b8c5e5" opacity=".25"/></pattern>
 <pattern id="boards" width="170" height="85" patternUnits="userSpaceOnUse" patternTransform="skewX(-20)"><path d="M0 0H170V85H0Z M85 0V42M0 42H170" stroke="#b8a9a0" stroke-width="1" opacity=".25" fill="none"/></pattern>
 <filter id="shadow" x="-30%" y="-30%" width="160%" height="170%"><feDropShadow dx="0" dy="9" stdDeviation="9" flood-color="#52628c" flood-opacity=".14"/></filter>
 <clipPath id="windowCut"><path d="M661 372V221Q661 133 787 133Q913 133 913 221V372Z"/></clipPath>
 <symbol id="whaleMotif" viewBox="0 0 100 50"><path d="M8 23Q25 3 47 20Q64 40 85 18L95 12L94 32L81 28Q66 53 37 46Q10 43 8 23Z" fill="currentColor"/><circle cx="24" cy="25" r="2" fill="#fff"/><path d="M34 13q-5-10-2-12m6 9q3-6 8-6" stroke="currentColor" stroke-width="3" fill="none"/></symbol></defs>
 <g id="room-camera"><path d="M25 30H1175V469L1200 760H0L25 469Z" fill="url(#wall)"/><path d="M25 30H1175V455H25Z" fill="url(#dots)"/><path d="M25 447H1175L1270 760H-70Z" fill="url(#floor)"/><path d="M25 447H1175L1270 760H-70Z" fill="url(#boards)"/><path d="M25 447H1175M25 433H1175" stroke="#a7b5d7" stroke-width="5"/><path d="M25 32V449M1175 32V449" stroke="#dee3f3" stroke-width="16"/><path d="M35 63H1165" stroke="#d9e0f2" stroke-width="9"/><path d="M35 71H1165" stroke="#fff" stroke-width="3"/>
 <path d="M80 80Q600 160 1120 80" stroke="#c4cce3" stroke-width="2" fill="none"/>
 <g fill="#f6d69a" stroke="#d6b97d">${[200,427,670,1010].map((x,i)=>`<path transform="translate(${x} ${[103,126,130,104][i]})" d="m0 0 3 9 10 1-8 6 2 10-7-6-8 6 3-10-8-6 10-1Z"/>`).join('')}</g>
 <g data-art="window"><path d="M647 388V221Q647 118 787 118Q927 118 927 221V388Z" fill="#bac9e6" stroke="#8396be" stroke-width="3"/>
 <g clip-path="url(#windowCut)"><rect x="655" y="125" width="265" height="255" fill="url(#sky)"/><circle id="sky-sun" cx="844" cy="203" r="28" fill="#fff2ba"/><g id="sky-moon"><circle cx="844" cy="202" r="25" fill="#fff8d9"/><circle id="moon-cutout" cx="854" cy="194" r="23" fill="#344a8b"/></g><g id="sky-stars" fill="#fff7ce"><circle cx="697" cy="211" r="2"/><circle cx="750" cy="158" r="2"/><circle cx="795" cy="254" r="1.5"/><circle cx="881" cy="246" r="2"/><circle cx="734" cy="275" r="1.5"/></g><g class="clouds" fill="#fff" opacity=".75"><path d="M674 245q-12-19 7-24 8-24 28-6 22-3 25 18h23q13 14-10 16Z"/><path d="M818 283q-7-14 8-20 13-20 27-4 20-5 23 15h23q12 12-8 15Z"/></g><path d="M650 335Q710 310 764 333T930 320V390H650Z" fill="#91b8d9"/><path d="M645 356Q731 336 818 359T935 346V391H645Z" fill="#739fc9"/><path d="M685 361h50m56 9h46m-92-21h34" stroke="#e4efff" stroke-width="2" opacity=".6"/></g>
 <path d="M787 135V375M662 272H913" stroke="#f9f9ff" stroke-width="9"/><path d="M641 383H933V399H641Z" fill="#edf0fb" stroke="#9fadd0" stroke-width="2"/>
 <path d="M634 138Q662 130 691 140L671 280Q657 311 645 360L624 365Z" fill="#b6c8ee" stroke="#94a6d0" stroke-width="2"/><path d="M691 140Q678 240 653 281M662 140Q649 246 640 276" fill="none" stroke="#e3eaff" stroke-width="6"/><path d="M940 138Q912 130 883 140L903 280Q917 311 929 360L950 365Z" fill="#b6c8ee" stroke="#94a6d0" stroke-width="2"/><path d="M883 140Q896 240 921 281M912 140Q925 246 934 276" fill="none" stroke="#e3eaff" stroke-width="6"/><path d="M633 281l35 4m236 0 35-4" stroke="#d7b46f" stroke-width="8"/></g>
 <g data-art="shelf" filter="url(#shadow)" stroke="#8b9cbd" stroke-width="2"><path d="M93 474V205Q93 170 130 170H294Q330 170 330 205V474Z" fill="url(#cabinet)"/><path d="M109 199H314V390H109Z" fill="#e6e9f3"/><g stroke-width="1">${books}</g><path d="M108 266H315M108 331H315M108 396H315" stroke="#b9c5df" stroke-width="9"/><rect x="108" y="412" width="94" height="48" rx="6" fill="#f8f7fc"/><rect x="215" y="412" width="99" height="48" rx="6" fill="#f8f7fc"/><circle cx="183" cy="436" r="4" fill="#d4b573"/><circle cx="233" cy="436" r="4" fill="#d4b573"/></g>
 <g transform="translate(165 139)" color="#809bd2"><use href="#whaleMotif" width="70" height="35"/></g>
 <g transform="translate(385 182)" stroke="#c5b286" stroke-width="3"><rect width="159" height="120" rx="8" fill="#fbf6ed"/><rect x="11" y="11" width="137" height="98" rx="3" fill="#dbe8fa" stroke="#fff"/><path d="M12 90q35-33 70-6t65-13v37H12" fill="#a8c3e8" stroke="none"/><circle cx="118" cy="38" r="16" fill="#fff7cc" stroke="none"/><g transform="translate(47 37)" color="#7497d1" stroke="none"><use href="#whaleMotif" width="68" height="39"/></g></g>
 <g transform="translate(1026 188)"><circle r="37" fill="#fcfaf5" stroke="#c5b58e" stroke-width="4"/><circle r="30" fill="none" stroke="#d8dfee"/><path id="clock-hands" stroke="#7485ad" stroke-width="3" fill="none" stroke-linecap="round"/><circle r="3" fill="#7485ad"/></g>
 <ellipse cx="586" cy="615" rx="270" ry="97" fill="#a8bce2" opacity=".24"/><ellipse cx="586" cy="604" rx="270" ry="97" fill="#e0e8fb" stroke="#c3cfea" stroke-width="3"/><ellipse cx="586" cy="604" rx="249" ry="80" fill="none" stroke="#fff9ef" stroke-width="5" stroke-dasharray="5 9"/><g opacity=".3" color="#829ed2" transform="translate(490 569)"><use href="#whaleMotif" width="175" height="83"/></g>
 <g data-art="rest" filter="url(#shadow)"><path d="M906 527V484Q906 458 931 458H1100Q1127 458 1127 484V561Z" fill="url(#cushion)" stroke="#8fa1c5" stroke-width="3"/><path d="M913 544h202v51H913Z" fill="#edf0fa" stroke="#9baac8" stroke-width="2"/><path d="M924 587v34m183-34v34" stroke="#b09d87" stroke-width="11" stroke-linecap="round"/><rect x="912" y="524" width="210" height="52" rx="23" fill="#c4d6f6" stroke="#9caed2" stroke-width="2"/><rect x="895" y="509" width="22" height="69" rx="10" fill="#b6cbed"/><rect x="1120" y="509" width="22" height="69" rx="10" fill="#b6cbed"/><path d="m937 493 40-11 24 30-46 12Z" fill="#fff5df" stroke="#d9c9a7" stroke-width="2"/></g>
 <g transform="translate(862 410)"><ellipse cx="0" cy="190" rx="32" ry="8" fill="#a6b1c4" opacity=".3"/><path d="M0 3v182" stroke="#ad9a7f" stroke-width="6"/><ellipse cy="184" rx="26" ry="6" fill="#c7b79b"/><path d="M-34 0H34L50 64H-50Z" fill="#fff3d4" stroke="#c9b797" stroke-width="2"/><ellipse cy="64" rx="50" ry="8" fill="#f4db9f"/><ellipse class="lamp-glow" cy="77" rx="100" ry="95" fill="url(#lamplight)"/></g>
 <g id="empty-desk" data-art="desk" filter="url(#shadow)" stroke="#b3a694" stroke-width="2"><path d="M430 516h204v20H430Z" fill="#fff9ed"/><path d="M441 537h182v40H441Z" fill="#f3e7d7"/><path d="M445 575v64m174-64v64" stroke-width="12"/><circle cx="534" cy="556" r="4" fill="#d7b770"/><path d="m494 507 44-8 47 8-45 9Z" fill="#fff" stroke="#acb9d5"/><path d="m538 500 2 16" stroke="#8c9bc0"/><path d="m595 505 23-5" stroke="#7589bc" stroke-width="4"/></g>
 <g transform="translate(75 500)"><path d="m0 0 47 3-6 47H7Z" fill="#eff4fc" stroke="#a1b4d2" stroke-width="2"/><ellipse cx="24" rx="24" ry="7" fill="#d9c8a5"/><path d="M23 0v-55m0 24L3-54m20 13 22-24" stroke="#87aaa0" stroke-width="4"/><path d="M22-38Q-8-35-8-65Q18-71 22-38M24-48Q25-81 50-80Q60-52 24-48M22-58Q0-84 22-101Q43-81 22-58" fill="#a3c5b8"/></g>
 <ellipse id="actor-shadow" cx="575" cy="580" rx="58" ry="12" fill="#5c6f9d" opacity=".12"/>
 <foreignObject id="actor-position" x="455" y="340" width="240" height="240"><canvas xmlns="http://www.w3.org/1999/xhtml" id="room-character" width="600" height="600" role="img" aria-label="大肥鱼正在待机"></canvas></foreignObject></g>`;
}
export class RoomScene{
 constructor(){
  $('#room-art').innerHTML=sceneMarkup();this.canvas=$('#room-character');this.ctx=this.canvas.getContext('2d');
  this.images=new Map();this.pose=[...ZONES.overview.pose];this.focus=[...ZONES.overview.focus];this.zone='overview';this.action='idle';this.elapsed=0;this.last=0;this.off=matchMedia('(prefers-reduced-motion: reduce)').matches;
  this.ready=Promise.all(['idle','walk','desk','shelf','window','rest'].map(async name=>{const image=new Image();image.src=`/character/${name}.png`;try{await image.decode();this.images.set(name,image);}catch{$('#asset-notice').hidden=false;}}));
  this.clock();this.clockTimer=setInterval(()=>this.clock(),30000);this.tick=this.tick.bind(this);this.raf=requestAnimationFrame(this.tick);
 }
 clock(date=new Date()){
  const p=dayPhase(date),colors={night:['#344a8b','#96a4d4'],dawn:['#e8b9c6','#f8e6cf'],day:['#8bb9f0','#e3efff'],sunset:['#c0a3d4','#f7c4a6']}[p.phase];
  document.body.dataset.time=p.phase;$('#room-clock').textContent=p.text;$('#room-clock').dateTime=date.toISOString();$('#room-daypart').textContent={night:'夜色轻轻 · 上海',dawn:'晨光初醒 · 上海',day:'日光正好 · 上海',sunset:'晚霞来信 · 上海'}[p.phase];
  $('#sky-top').setAttribute('stop-color',colors[0]);$('#sky-bottom').setAttribute('stop-color',colors[1]);$('#moon-cutout').setAttribute('fill',colors[0]);$('#sky-moon').style.display=$('#sky-stars').style.display=p.phase==='night'?'':'none';$('#sky-sun').style.display=p.phase==='night'?'none':'';
  const hand=(a,l)=>`${Math.sin(a)*l} ${-Math.cos(a)*l}`;$('#clock-hands').setAttribute('d',`M${hand((p.hour%12+p.minute/60)*Math.PI/6,17)}L0 0L${hand(p.minute*Math.PI/30,25)}`);
 }
 go(zone){
  if(!ZONES[zone])return;const dest=ZONES[zone];this.zone=zone;
  if(this.off){this.pose=[...dest.pose];this.focus=[...dest.focus];this.transition=null;this.action=dest.action;}
  else{this.transition={start:this.elapsed,fromPose:[...this.pose],fromFocus:[...this.focus],dest,duration:1400};this.direction=dest.pose[0]>=this.pose[0]?1:-1;this.action='walk';}
  $('#character-action').textContent=dest.label;this.canvas.setAttribute('aria-label',`大肥鱼：${dest.label}`);this.draw();
 }
 setPaused(value){this.off=value;if(value&&this.transition)this.go(this.zone);this.draw();}
 tick(now){const delta=Math.min(48,now-(this.last||now));this.last=now;if(!document.hidden){if(!this.off)this.elapsed+=delta;if(this.transition){const t=Math.min(1,(this.elapsed-this.transition.start)/this.transition.duration),u=ease(t),tr=this.transition;this.pose=tr.fromPose.map((v,i)=>mix(v,tr.dest.pose[i],u));this.focus=tr.fromFocus.map((v,i)=>mix(v,tr.dest.focus[i],u));if(t===1){this.action=tr.dest.action;this.transition=null;}}this.draw();}this.raf=requestAnimationFrame(this.tick);}
 frame(){const t=this.elapsed; if(this.off)return 0;if(this.action==='idle'){const s=t%5200;return s<3900?0:s<4020?3:s<4140?4:s<4260?5:s<4500?6:7;}return Math.floor(t/({walk:110,desk:220,shelf:440,window:720,rest:650}[this.action]||500))%8;}
 draw(){
  const [cx,cy,z]=this.focus;$('#room-camera').setAttribute('transform',`translate(${600-cx*z} ${380-cy*z}) scale(${z})`);
  const [x,y,size]=this.pose,fo=$('#actor-position');fo.setAttribute('x',x-size/2);fo.setAttribute('y',y-size);fo.setAttribute('width',size);fo.setAttribute('height',size);
  $('#actor-shadow').setAttribute('cx',x);$('#actor-shadow').setAttribute('cy',y-7);$('#actor-shadow').setAttribute('opacity',this.action==='rest'?'.04':'.12');$('#empty-desk').style.opacity=this.action==='desk'?'0':'1';
  this.canvas.dataset.action=this.action;this.canvas.dataset.zone=this.zone;const frame=this.frame();this.canvas.dataset.frame=String(frame);
  this.canvas.dataset.assetReady=String(this.images.has(this.action));
  const img=this.images.get(this.action)||this.images.get('idle');if(!img)return;
  if(img.width!==ATLAS.width||img.height!==ATLAS.height){$('#asset-notice').hidden=false;return;}
  const c=this.ctx,w=this.canvas.width,h=this.canvas.height,sw=ATLAS.cell,sh=ATLAS.cell,
        sx=ATLAS.pad+(frame%4)*(ATLAS.cell+ATLAS.gap),sy=ATLAS.pad+Math.floor(frame/4)*(ATLAS.cell+ATLAS.gap);
  c.clearRect(0,0,w,h);c.save();if(this.action==='walk'&&this.direction<0){c.translate(w,0);c.scale(-1,1);}const breath=this.off||this.action==='walk'?0:Math.sin(this.elapsed/850)*1.2;
  c.drawImage(img,sx,sy,sw,sh,0,breath,w,h-breath);c.restore();
 }
}
