import {createPublicUrls} from './public-path.js';
const publicUrls = createPublicUrls(globalThis);
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);let project=params.get('project')||'',current=params.get('result_ref')||'',result;
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;};
function error(e){$('error').hidden=false;$('error').textContent=e.message||e;}
async function api(op,args={},write=false){const data={project,...args};const url=publicUrls.route('/natural-data/api/')+op;const r=await fetch(write?url:url+'?'+new URLSearchParams(data),write?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)}:{});const v=await r.json();if(!r.ok)throw new Error(v.error||v.detail||'服务不可用');return v;}
function table(columns,rows){const wrap=el('div',undefined,'table-wrap'),t=el('table'),head=el('tr');for(const c of columns)head.append(el('th',result?.column_labels?.[c]||c));t.append(head);for(const row of rows){const tr=el('tr');for(const c of columns){const td=el('td',row[c]??'');td.title=String(row[c]??'');tr.append(td);}t.append(tr);}wrap.append(t);return wrap;}
function svgNode(tag,attrs={}){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n;}
function draw(rows,columns,kind){
 const nums=columns.filter(c=>rows.some(r=>r[c]!==null&&r[c]!==''&&Number.isFinite(Number(r[c])))&&rows.every(r=>r[c]===null||r[c]===''||Number.isFinite(Number(r[c]))));
 const labels=columns.find(c=>!nums.includes(c))||columns[0],metric=nums.find(c=>c!==labels)||nums[0];
 if(!metric)return el('p','当前结果没有可绘制的数值字段，请查看表格。');
 const data=rows.slice(0,40),svg=svgNode('svg',{viewBox:'0 0 760 320',role:'img','aria-label':kind+' 数据图表'});
 const values=data.map(r=>Number(r[metric])||0),min=Math.min(0,...values),max=Math.max(1,...values),scale=v=>260-(v-min)/(max-min)*210;
 const title=svgNode('text',{x:35,y:20,fill:'#57655d','font-size':12});title.textContent=(result.column_labels?.[metric]||metric)+(rows.length>40?'（前 40 项）':'');svg.append(title);
 if(kind==='pie'||kind==='donut'){
  if(values.some(v=>v<0))return el('p','含负数的数据不适合饼图，请切换柱状图或表格。');
  const total=values.reduce((a,b)=>a+b,0);if(!total)return el('p','数据合计为 0，无法绘制占比。');let angle=-Math.PI/2;
  data.forEach((r,i)=>{const next=angle+values[i]/total*2*Math.PI,fill=['#497b65','#8caf97','#c7d5b2','#ccad77','#93aabd','#b797a0'][i%6];
   const slice=values[i]===total?svgNode('circle',{cx:220,cy:160,r:110,fill}):svgNode('path',{d:`M220,160 L${220+110*Math.cos(angle)},${160+110*Math.sin(angle)} A110,110 0 ${next-angle>Math.PI?1:0},1 ${220+110*Math.cos(next)},${160+110*Math.sin(next)} Z`,fill});svg.append(slice);angle=next;
   if(i<10){const text=svgNode('text',{x:390,y:45+i*24,fill,'font-size':12});text.textContent=String(r[labels])+' · '+values[i];svg.append(text);}
  });if(kind==='donut')svg.append(svgNode('circle',{cx:220,cy:160,r:60,fill:'white'}));return svg;
 }
 const points=[];data.forEach((r,i)=>{const x=55+(i+.5)*660/data.length,y=scale(values[i]);points.push(`${x},${y}`);
  const shape=['line','area','scatter'].includes(kind)?svgNode('circle',{cx:x,cy:y,r:4,fill:'#497b65'}):svgNode('rect',{x:x-220/data.length,y:Math.min(y,scale(0)),width:440/data.length,height:Math.max(1,Math.abs(scale(0)-y)),rx:2,fill:'#497b65'});
  const tip=svgNode('title');tip.textContent=String(r[labels])+': '+values[i];shape.append(tip);svg.append(shape);
  if(data.length<=14||i%Math.ceil(data.length/12)===0){const text=svgNode('text',{x,y:285,'text-anchor':'middle','font-size':10,fill:'#717b74'});text.textContent=String(r[labels]).slice(0,12);svg.append(text);}
 });if(['line','area'].includes(kind))svg.append(svgNode('polyline',{points:points.join(' '),fill:'none',stroke:'#497b65','stroke-width':2}));return svg;
}
function pivot(r){const numeric=r.columns.filter(c=>r.rows.some(x=>typeof x[c]==='number'));const dims=r.columns.filter(c=>!numeric.includes(c));if(dims.length<2||!numeric.length)return table(r.columns,r.rows);const [a,b]=dims,v=numeric[0],cols=[...new Set(r.rows.map(x=>String(x[b])))],map=new Map();for(const row of r.rows){const key=String(row[a]);if(!map.has(key))map.set(key,{[a]:key});const target=map.get(key);target[String(row[b])]=(target[String(row[b])]||0)+Number(row[v]||0);}return table([a,...cols],Array.from(map.values()));}
async function show(id){current=id;result=await api('result',{result_ref:id});const a=$('result');a.replaceChildren();a.append(el('h2',result.title));a.append(el('span',result.status,'badge'));a.append(el('p',`${result.row_count||0} 行${result.truncated?' · 结果已截断，请缩小范围后重查':''}`,'muted'));
 if(result.status!=='succeeded'){a.append(el('p',result.error||'查询未完成'));return;}
 const toolbar=el('div',undefined,'toolbar'),select=el('select');for(const [value,label]of Object.entries({table:'表格',bar:'柱状图',line:'折线图',pie:'饼图',donut:'环形图',scatter:'散点图','pivot-table':'透视表',map:'地图'})){const o=el('option',label);o.value=value;select.append(o);}select.value=result.view||'table';select.onchange=async()=>{try{await api('visualize',{result_ref:id,kind:select.value},true);await show(id);}catch(e){error(e);}};toolbar.append(select);a.append(toolbar);
 if(select.value==='pivot-table')a.append(pivot(result));
 else if(select.value==='map'){a.append(el('p','地图需要返回经纬度或 GeoJSON 字段；完整空间渲染由地图组件提供。'));a.append(table(result.columns,result.rows));}
 else {if(select.value!=='table')a.append(draw(result.rows,result.columns,select.value));a.append(table(result.columns,result.rows));}
 const d=el('details'),summary=el('summary','查询依据与执行凭证');d.append(summary,el('pre',result.sql+'\n\n'+JSON.stringify(result.receipt,null,2)));a.append(d);}
async function refresh(){if(!project)return;const list=await api('results');$('history').replaceChildren();for(const r of list){const b=el('button',r.title+(r.status==='succeeded'?'':' · '+r.status),current===r.result_ref?'active':'');b.onclick=()=>show(r.result_ref).catch(error);$('history').append(b);}if(current)await show(current);}
async function init(){if(current)document.body.classList.add('compact');if(params.has('settings')){document.querySelector('.workspace').hidden=true;document.querySelector('nav').hidden=true;document.querySelector('header').hidden=true;$('configuration').hidden=false;}const cfg=await api('config');const projects=Object.keys(cfg.projects);if(project&&!projects.includes(project))projects.push(project);for(const p of projects){const o=el('option',p.split('/').pop()||p);o.value=p;o.title=p;$('project').append(o);}project=project||projects[0]||'';$('project').value=project;$('project-path').value=project;
 for(const s of cfg.sources){const label=el('label'),check=el('input');check.type='checkbox';check.value=s.id;check.checked=(cfg.projects[project]||[]).includes(s.id);label.append(check,document.createTextNode(s.name+' · '+s.dialect));$('source-options').append(label);}
 $('project').onchange=()=>{project=$('project').value;current='';refresh().catch(error);};$('refresh').onclick=()=>refresh().catch(error);$('settings').onclick=()=>{$('configuration').hidden=!$('configuration').hidden;};$('save-binding').onclick=async()=>{try{project=$('project-path').value.trim();await api('bind',{source_ids:[...$('source-options').querySelectorAll('input:checked')].map(n=>n.value)},true);$('save-message').textContent=' 已保存。关闭窗口后继续当前对话即可。';}catch(e){error(e);}};await refresh();}
init().catch(error);
