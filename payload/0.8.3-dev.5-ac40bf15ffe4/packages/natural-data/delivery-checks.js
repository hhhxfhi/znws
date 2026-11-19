// Explicit deliverables, checked against persisted calculations. This does not
// claim to certify arbitrary language or infer an unstated business definition.
const numberIn=(text,value)=>Number.isFinite(value)&&[...text.replaceAll(',','').replace(/[−‐‑‒–](?=\d)/g,'-').matchAll(/-?\d+(?:\.\d+)?/g)].some(m=>Math.abs(Number(m[0])-value)<=.011);
const changeIn=(text,value)=>numberIn(text,value)||(value<0&&[...text.matchAll(/(?:下降|减少|降低|降幅)(?:了|为|约|是)?\s*(\d+(?:\.\d+)?)\s*%/g)].some(m=>Math.abs(Number(m[1])+value)<=.011));
const notTriggered=/未触发|不触发|未达到|未超过|无需预警/;
const triggered=/已触发|已达到|已超过|触发预警|需要预警/;
function groupLabels(claim){
 const dims=claim.current?.dimensions||claim.baseline?.dimensions;
 if(dims)return Object.entries(dims).filter(([key])=>key!=='period').map(([,value])=>String(value??'未分类'));
 return typeof claim.group==='string'?[claim.group]:Object.values(claim.group||{}).map(String);
}
function groupIssues(text,totals){
 const groups=totals.map(claim=>({claim,labels:groupLabels(claim)}));
 const segments=text.replaceAll('**','').split(/[。；;\n]/);
 const issues=[];
 for(const {claim,labels} of groups){
  const title=labels.join(' / ')||'未标识分组';
  // A clause/table row must identify exactly one dimension tuple. Shared or
  // missing labels cannot authorize borrowing another group's percentage.
  const own=segments.filter(s=>labels.length&&labels.every(label=>s.includes(label))&&
   groups.filter(g=>g.labels.length&&g.labels.every(label=>s.includes(label))).length===1).join('；');
  if(!own){issues.push('交付缺项：请单独列出“'+title+'”的合计变化率及阈值判断，不能省略或混用分组。');continue;}
  const rates=[...own.matchAll(/(?:同比|变化率|增长|增幅|下降|降幅|减少)(?:增长|下降|为|是|约|了|[:：]|\s)*([−‐‑‒–-]?\d+(?:\.\d+)?)\s*[%％]/g)]
   .map(m=>{const n=Number(m[1].replace(/[−‐‑‒–]/g,'-'));return /下降|降幅|减少/.test(m[0])?-Math.abs(n):n;});
  const percentages=[...own.matchAll(/([−‐‑‒–-]?\d+(?:\.\d+)?)\s*[%％]/g)].filter(m=>
   !/(?:阈值|门槛|超过|高于|低于)\s*(?:为|是|[:：])?\s*$/.test(own.slice(0,m.index))&&
   !/^\s*(?:的)?(?:阈值|门槛)/.test(own.slice(m.index+m[0].length)))
   .map(m=>Number(m[1].replace(/[−‐‑‒–]/g,'-')));
  const supplied=rates.length?rates:percentages;
  if(Number.isFinite(claim.change_percent)&&(!supplied.length||!supplied.some(n=>Math.abs(n-claim.change_percent)<=.011)||rates.some(n=>Math.abs(n-claim.change_percent)>.011)))
   issues.push('分组核验：“'+title+'”的期间合计变化率应为'+Number(claim.change_percent.toFixed(2))+'%，请核对组名与数值，不借用其他组。');
  if(claim.change_percent===null&&(!/(?:基期|分母).{0,16}(?:零|0)|无法计算|不能计算/.test(own)||rates.length))
   issues.push('分组核验：“'+title+'”基期为零，变化率无法计算，不能写成0%。');
  if(claim.change_threshold!==undefined){
   if(claim.change_matched===true&&(!triggered.test(own)||notTriggered.test(own)))
    issues.push('分组核验：“'+title+'”已触发阈值，需在本组明确写出。');
   if(claim.change_matched===false&&(!notTriggered.test(own)||triggered.test(own.replace(new RegExp(notTriggered.source,'g'),''))))
    issues.push('分组核验：“'+title+'”未触发阈值，需在本组明确写出。');
   if(claim.change_matched===null&&(!/无法(?:计算|判断|判定)|不能(?:计算|判断|判定)/.test(own)||notTriggered.test(own)||triggered.test(own)))
    issues.push('分组核验：“'+title+'”阈值无法判断，不能判定已触发或未触发。');
  }
 }
 return issues;
}
// An explicit request for each category's share must not be satisfied by its
// month-on-month growth rate. Reuse complete, additive facts and server claims;
// never combine different periods or infer a denominator for overlapping groups.
export function categoryShareIssues(text,evidence,userText){
 const request=userText.split(/[。？?；;\n]/).find(s=>/(?:各|分别|各自)占(?:总量|总数|比)|(?:各类|每类|分别).{0,8}占比/.test(s));
 if(!request)return [];
 const before=request.split(/(?:各|分别|各自)占|占比/)[0];
 const month=[...before.matchAll(/(?:(\d{4})年)?(\d{1,2})月/g)].at(-1);
 const issues=[],seen=new Set();
 for(const e of evidence){
  const contract=e.metric_contract;
  if(!e.result_ref||seen.has(e.result_ref)||contract?.additive!==true||contract.ratio||e.truncated)continue;
  seen.add(e.result_ref);
  const dims=contract.scope?.dimensions||[];
  if(dims.length!==1||(contract.overlapping_dimensions||[]).includes(dims[0]))continue;
  const periods=[...new Set((e.facts||[]).map(f=>f.dimensions?.period))];
  const selected=month?periods.filter(p=>typeof p==='string'&&p.endsWith('-'+month[2].padStart(2,'0'))&&(!month[1]||p.startsWith(month[1]+'-'))):periods;
  if(selected.length!==1)continue;
  const facts=(e.facts||[]).filter(f=>f.dimensions?.period===selected[0]);
  if(facts.length<2||facts.length>20||facts.some(f=>!Number.isFinite(f.value)||f.value<0))continue;
  const labels=facts.map(f=>String(f.dimensions[dims[0]]??''));
  if(labels.some(l=>!l)||new Set(labels).size!==labels.length)continue;
  const claims=evidence.flatMap(r=>[...(r.claims||[]),...(r.verified_claims||[])]);
  const shares=facts.map(f=>claims.find(c=>c.fact?.id===f.id&&Object.hasOwn(c,'share_percent')));
  if(shares.some(c=>!c)){
   issues.push('交付缺项：用户要求各类占总量的比例，增减量和变化率不能替代占比。复用已有结果 '+e.result_ref+' 调用 analysis_compute operation=share（按期间分别计算），正文逐类给出'+selected[0]+'的占比，不重复查询。');
   continue;
  }
  const aliases=labels.map(label=>[label,...(request.includes(label.slice(0,2))&&labels.filter(l=>l.startsWith(label.slice(0,2))).length===1?[label.slice(0,2)]:[])]);
  const nextLabel=aliases.flat().map(l=>l.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
  const segments=text.replaceAll('**','').replace(new RegExp('[，,](?=\\s*(?:'+nextLabel+'))','g'),'\n').split(/[。；;\n]/);
  for(let i=0;i<shares.length;i++){
   const own=segments.filter(s=>aliases[i].some(l=>s.includes(l))&&aliases.filter(a=>a.some(l=>s.includes(l))).length===1).join('；');
   const value=shares[i].share_percent;
   const percentages=[...own.matchAll(/(-?\d+(?:\.\d+)?)\s*[%％]/g)].map(m=>Number(m[1]));
   if(value===null?!/无法计算|不能计算|分母为零|分母为0/.test(own):!percentages.some(n=>Math.abs(n-value)<=.011))
    issues.push('交付缺项：请在“'+labels[i]+'”对应行明确给出'+selected[0]+'占比'+(value===null?'无法计算（分母为零）':Number(value.toFixed(2))+'%')+'，不可省略或借用其他类别的比例。');
  }
 }
 return issues;
}
export function periodDeliveryIssues(text,evidence,userText){
 const issues=categoryShareIssues(text,evidence,userText);
 const annual=evidence.some(e=>e.status==='succeeded'&&e.validation?.period_grain?.state==='checked'
    &&e.validation.period_grain.actual==='year');
 const monthly=evidence.some(e=>e.status==='succeeded'&&e.validation?.period_grain?.state==='checked'
    &&e.validation.period_grain.actual==='month');
 if(annual&&!monthly&&/(?:按\s*(?:年度|年份|年)|逐年|各年)/.test(userText)
    &&!/(?:另|并|同时)[^。；\n]{0,12}(?:逐月|按月|月度)/.test(userText)){
  const heading=/^\s*#{1,6}\s*[^\n]{0,55}(?:逐月|月度|各月|按月)[^\n]{0,35}$/m;
  const monthlyRows=text.split('\n').filter(line=>/^\s*\|\s*(?:\*{1,2}\s*)?(?:19|20)\d{2}(?:[-/]\d{1,2}|年\s*\d{1,2}\s*月)\s*(?:\*{1,2}\s*)?\|/.test(line));
  if(heading.test(text)||monthlyRows.length>=2)
   issues.push('原问要求按年度，已核实的结果也按年度汇总。正文标题和表格请用每年的标签及该年值，保留已有年度图，不把年度首日或月份明细当作展示粒度。');
 }
 const pair=userText.match(/(\d{4})年(\d{1,2})月[^。；？?]{0,40}比(?:(\d{4})年)?(\d{1,2})月/);
 if(pair&&/总变化|总量|变化多少/.test(userText))for(const r of evidence){
  const c=r.metric_contract,rows=r.facts||[];
  if(c?.additive!==true||!c.unit||c.ratio||r.truncated||rows.some(f=>!Number.isFinite(f.value))||(c.overlapping_dimensions||[]).length)continue;
  const periods=[...new Set(rows.map(f=>f.dimensions?.period))];
  const current=periods.filter(p=>p===pair[1]+'-'+pair[2].padStart(2,'0'));
  const before=periods.filter(p=>typeof p==='string'&&p.endsWith('-'+pair[4].padStart(2,'0'))&&(!pair[3]||p.startsWith(pair[3]+'-')));
  if(current.length!==1||before.length!==1||current[0]===before[0])continue;
  const groups=period=>rows.filter(f=>f.dimensions?.period===period);
  const a=groups(current[0]),b=groups(before[0]);
  const keys=xs=>xs.map(f=>JSON.stringify(Object.entries(f.dimensions).filter(([k])=>k!=='period').sort())).sort();
  if(a.length<2||new Set(keys(a)).size!==a.length||JSON.stringify(keys(a))!==JSON.stringify(keys(b)))continue;
  const delta=a.reduce((n,f)=>n+f.value,0)-b.reduce((n,f)=>n+f.value,0);
  if(!numberIn(text,Math.abs(delta)))issues.push('交付缺项：用户要求总体变化，需明确给出两期分类合计的总差额'+Math.abs(delta)+c.unit+'及增减方向；只列各类差额不足以完成总体问题。复用现有结果，不重新查询。');
 }
 const results=evidence.filter(e=>e.delivery?.kind==='period_comparison'||e.metric_contract?.additive_over_time&&e.facts?.length);
 if(!results.length||!/(?:同比|同期|对比|比较)/.test(userText)||!/(?:合计|总计|总量|季度)/.test(userText))return issues;
 const claims=evidence.flatMap(e=>e.claims||e.verified_claims||[]);
 const totals=claims.filter(c=>c.operation==='aggregate_compare');
 const partial=results.find(e=>e.delivery?.status==='partial');
 if(!totals.length){
  if(partial&&/(?:未完成|无法|不能|尚未|未能)/.test(text))return issues;
  const refs=[...new Set(results.map(e=>e.result_ref).filter(Boolean))];
  issues.push('交付缺项：用户要求期间合计同比，当前只有原值，缺少逐期差额、变化率及合计比较。复用已有结果'+refs.join('、')+'调用analysis_compute aggregate_compare；两份结果填comparison_result_ref，不重复查询、不再次确认已明确的普通算法。');
  return issues;
 }
 // A single total needs no group label; multiple totals must retain their keys.
 if(totals.length===1){
  const total=totals[0];
  if(Number.isFinite(total.change_percent)&&!changeIn(text,total.change_percent))
   issues.push('交付缺项：正文需给出已核算的期间合计变化率'+Number(total.change_percent.toFixed(2))+'%，不可只列原值或让用户自行计算。');
  if(total.change_percent===null&&!/(?:基期|分母).{0,16}(?:零|0)|(?:同比|变化率).{0,12}(?:无法|不能)|(?:无法|不能).{0,12}(?:同比|变化率)/.test(text))
   issues.push('交付缺项：基期为零，变化率无法计算，不能写成0%或据此判断未触发。');
  if(total.change_threshold!==undefined){
   if(total.change_matched===false&&/只有[^。；;\n]{0,80}才[^。；;\n]{0,60}(?:增量|增长|增加量)最大/.test(userText)&&
      text.replaceAll('**','').split(/[。；;\n，,]/).some(s=>/(?:增量|增长|增加量)最大[^。；;\n]{0,18}(?:\d{1,2}\s*月|202\d-\d{2})|(?:\d{1,2}\s*月|202\d-\d{2})[^。；;\n]{0,18}(?:增量|增长|增加量)最大/.test(s)&&!/(?:未|不|无需|不要|不能|没有|若|如果)[^，,]{0,18}(?:指出|列出|判断|报告|触发|增量|增长|增加量)/.test(s)))
    issues.push('条件分支：合计未触发，用户仅要求触发时指出增量最大月份。保留逐月对照和未触发结论，不追加未命中分支的最大月份解读。');
   if(total.change_matched===false&&!/未触发|不触发|未达到|未超过|无需预警/.test(text))issues.push('交付缺项：按已核算阈值判断，明确写“未触发”，不要再要求用户确认已有规则。');
   if(total.change_matched===true&&(!/(?:已触发|已达到|已超过|触发预警|需要预警)/.test(text)||/(?:未触发|不触发|未达到|未超过|无需预警)/.test(text)))issues.push('交付缺项：合计变化率已满足阈值，需明确给出已触发结论。');
  }
 }else if(totals.length>1){
  issues.push(...groupIssues(text,totals));
 }
 return issues;
}
