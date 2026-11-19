import {createPublicUrls} from '../public-path.js';
const publicUrls = createPublicUrls(globalThis);
import React,{memo,useEffect,useLayoutEffect,useMemo,useState,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import ProductVisualization from './ProductVisualization';
import ForecastSection from './ForecastSection';
import {forecastView, scopedResultClaims, scopedResultLimitations, splitClaimNotes, trainingChartCollapsed} from './forecast-model';
import {conciseLabel,missingChartUnit} from '../result-labels.js';
import {comparisonPresentation} from '../comparison-presentation.js';
import {mergeViewPatch,missingMonthNotes,monthlyDisplayResult,resultDisplayTitle} from '../result-view.js';
import {publicMetricLabel} from '../calculation-presentation.js';
import './product.css';
import './result.css';
const documentId=crypto.randomUUID();
const DATA_VISUALIZATION={type:'table' as const,encoding:{}};
const names:Record<string,string>={table:'表格',kpi:'指标卡',bar:'条形图',column:'柱状图','grouped-bar':'分组柱状图','stacked-bar':'堆叠柱状图','percent-stacked-bar':'百分比堆叠图',line:'折线图',area:'面积图',pie:'饼图',donut:'环形图',scatter:'散点图',bubble:'气泡图',heatmap:'矩阵热力图',treemap:'树图',histogram:'直方图',radar:'雷达图',funnel:'漏斗图',gauge:'仪表盘',waterfall:'瀑布图',boxplot:'箱线图','alert-table':'预警表','pivot-table':'多维透视表'};
// 0.6.2: one short sentence naming what cannot be computed and why; the table cell
// itself says "无法计算", so the note no longer repeats "空值不表示零".
function unknownNote(result:any):string{
  const reasons=(result.null_reasons||[]) as Array<{dimensions?:Record<string,unknown>;reason?:string}>;
  if(!reasons.length)return '部分数值无法计算，原因未确认。';
  const label=(r:{dimensions?:Record<string,unknown>})=>Object.entries(r.dimensions||{}).filter(([k])=>k!=='period').map(([,v])=>String(v)).join('·');
  const groups=new Map<string,string[]>();
  for(const r of reasons){const key=r.reason||'原因未确认';groups.set(key,[...(groups.get(key)||[]),label(r)].filter(Boolean));}
  return [...groups].map(([reason,names])=>(names.length?names.slice(0,5).join('、')+(names.length>5?' 等 '+names.length+' 项':'')+'无法计算：':'无法计算：')+reason).join('；')+'。';
}
const AnswerBasis=memo(function AnswerBasis({result,limitations}:{result:any;limitations:string[]}) {
 const contract=result.answer_contract||{};
 const gaps=missingMonthNotes(result);
 const ranking=contract.kind==='historical_profession_ranking';
 const periods=Array.isArray(contract.periods)?contract.periods.join('、'):'';
 const scope=ranking&&contract.cutoff?'截至 '+contract.cutoff:periods;
 const summary=[scope,contract.unit].filter(Boolean).join(' · ');
 const statements=ranking&&Array.isArray(contract.statements)?contract.statements:[];
 const notes=[...new Set(limitations.filter((text:string)=>!/无法确认的数值|保留为空|不按零处理/.test(text)))];
 if(!summary&&!statements.length&&!gaps.length&&!notes.length)return null;
 return <section className="nw-verified-claims" aria-label="已验证数据回答">
   <h3>数据与口径</h3>{summary&&<p>{summary}</p>}
   {statements.map((s:any)=><p key={s.statement_id}>{s.text}</p>)}
   {gaps.map(text=><p key={text}>{text}</p>)}
   {notes.map((text:string)=><p key={text}>{text}</p>)}
 </section>;
});
const AnalysisFacts=memo(function AnalysisFacts({result,claims,limitations}:{result:any;claims:any[];limitations:string[]}) {
 const comparison=comparisonPresentation({...result,verified_claims:claims});
 if(!claims.length&&result.delivery?.status!=='partial')return null;
 // 0.8.2: keep the first few conclusions visible and fold the rest behind a
 // summary line; duplicate limitation notes are merged (customer feedback Q4).
 const sections=comparison?{visible:comparison.notes,folded:[]}:splitClaimNotes(claims
   .filter((c:any)=>c.verified_text)
   .map((c:any)=>c.public_text||(c.operation==='matrix_method'?'分析已完成，详细计算依据可在“查询依据”中查看。':c.verified_text)));
 return <section className="nw-analysis-delivery" aria-label="已核算分析结论">
  <h3>{comparison?comparison.title:'已核算结论'}</h3>
  {comparison&&<ProductVisualization result={comparison} visualization={comparison.chart} runId={result.result_ref+'-comparison'}/>}
  {sections.visible.map((text:string,i:number)=><p key={i}>{text}</p>)}
  {sections.folded.length>0&&<details className="nw-analysis-more">
    <summary>其余 {sections.folded.length} 条已核算结论</summary>
    {sections.folded.map((text:string,i:number)=><p key={i}>{text}</p>)}
  </details>}
  {result.delivery?.status==='partial'&&<p role="note">部分计算尚未完成：{result.delivery.reason}</p>}
  {[...new Set(limitations)].map(text=><p key={text}>{text}</p>)}
 </section>;
});
const ResultReceipt=memo(function ResultReceipt({result,claims}:{result:any;claims:any[]}) {
 const actual=result.chart?.type||'table', requested=result.visualization?.requested;
 const fallback=Boolean(result.visualization?.fallback&&requested&&requested!==actual);
 const missingUnit=missingChartUnit(result);
 const evidence=result.report?{receipt:result.receipt,report_revision:result.report.report_revision,spec:result.report.spec}
   :{...result.receipt,analysis:claims,analysis_limitations:result.claim_limitations};
 return <section className="nw-receipt" aria-label="查询依据与执行凭证">
   {result.source_provenance?.synthetic===true&&<p className="nw-receipt-note">数据来源为合成库{result.source_provenance.name?' · '+result.source_provenance.name:''}</p>}
   {fallback&&<p className="nw-receipt-note">请求图形为{names[requested]||requested}，当前结果使用{names[actual]||actual}展示。</p>}
   {missingUnit&&<p className="nw-receipt-note">数值单位未登记，当前按原字段值展示。</p>}
   <pre>{result.sql+'\n\n'+JSON.stringify(evidence,null,2)}</pre>
 </section>;
});
function relabelEncoding(node:any, labels:Record<string,string>){
 if(!node||typeof node!=='object')return;
 if(node.field&&labels[node.field])node.label=labels[node.field];
 for(const part of Object.values(node))if(typeof part==='object')relabelEncoding(part,labels);
}
function App(){
 const params=new URLSearchParams(location.search),project=params.get('project')||'',ref=params.get('result_ref')||'';
 const embedded=params.get('embedded')==='1';
 const [result,setResult]=useState<any>(null),[error,setError]=useState('');
 const [tabs,setTabs]=useState(()=>{const active=params.get('tab')||'chart';return {active,visited:[active]};});
 const {active:tab,visited}=tabs;
 const accepted=useRef<any>(null);
 const main=useRef<HTMLElement>(null);
 const savedScroll=useRef(new WeakMap<HTMLElement,{top:number;left:number}>());
 const lastFocused=useRef<HTMLElement|null>(null);
 const scrollableNodes=()=>main.current?.querySelectorAll<HTMLElement>('.nw-chart-viewport,.product-table-wrap,.product-bar-wrap,.product-pivot-wrap,.product-pivot-canvas,.nw-receipt pre')||[];
 const visible=(node:HTMLElement)=>!node.closest('[hidden]')&&node.getClientRects().length>0;
 function selectTab(next:string){
  if(main.current?.contains(document.activeElement)&&document.activeElement instanceof HTMLElement)lastFocused.current=document.activeElement;
  for(const node of scrollableNodes()){
   if(visible(node))savedScroll.current.set(node,{top:node.scrollTop,left:node.scrollLeft});
  }
  setTabs(prev=>prev.active===next?prev:{active:next,visited:prev.visited.includes(next)?prev.visited:[...prev.visited,next]});
 }
 useLayoutEffect(()=>{
  // React 18 does not forward the boolean inert prop. Keep clipped panels out of focus.
  for(const panel of main.current?.querySelectorAll<HTMLElement>('.nw-table-panel')||[]){
   panel.inert=panel.hidden;
  }
  for(const node of scrollableNodes()){
   const position=savedScroll.current.get(node);
   if(position&&visible(node)){node.scrollTop=position.top;node.scrollLeft=position.left;}
  }
  const focus=lastFocused.current;
  if(document.hasFocus()&&document.activeElement===document.body&&focus?.isConnected&&visible(focus)){
   focus.focus({preventScroll:true});
  }
 },[tab,result]);
 async function api(op:string,body?:any){const u=publicUrls.route('/natural-data/api/'+op);const r=await fetch(body?u:u+'?'+new URLSearchParams({project,result_ref:ref}),body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({project,result_ref:ref,...body})}:{});const v=await r.json();if(!r.ok)throw Error(v.error||'结果读取失败');return v;}
 function accept(value:any):boolean{
  if(value.result_ref!==ref)return false;
  if(accepted.current&&((value.view_revision||0)<(accepted.current.view_revision||0)||(value.report?.report_revision||0)<(accepted.current.report?.report_revision||0)))return false;
  // postMessage and JSON parsing already transfer ownership of rows. Only
  // copy the small display metadata that is normalized below.
  const labels={...value.column_labels};
  const concise=conciseLabel;
  for(const field of value.columns||[]){
   if(labels[field]&&!value.metric_contract)labels[field]=concise(labels[field],value.column_unit_evidence?.[field]?.unit);
   else if(labels[field]&&value.metric_contract)labels[field]=publicMetricLabel(labels[field],value);
   else {const match=/^(avg|sum|total|min|max)_(.+)$/.exec(field);if(match&&labels[match[2]])labels[field]=({avg:'平均',sum:'合计',total:'合计',min:'最小',max:'最大'} as any)[match[1]]+concise(labels[match[2]]);}
  }
  const chart=value.chart?structuredClone(value.chart):value.chart;
  relabelEncoding(chart?.encoding,labels);
  accepted.current={...value,title:publicMetricLabel(value.title,value),chart,column_labels:labels};
  setResult(accepted.current);
  return true;
 }
 useEffect(()=>{
  if(!embedded){api('result').then(accept).catch(e=>setError(e.message));return;}
  const receive=(event:MessageEvent)=>{
   if(event.origin!==location.origin||event.source!==parent)return;
   const message=event.data;
   if(message?.type==='natural-data:state'){
    if(!message.result||!accept(message.result))return;
   }else if(message?.type==='natural-data:view'){
    const current=accepted.current;
    const next=mergeViewPatch(current,message.patch);
    if(!next){
     if(message.patch?.result_ref===ref&&(!current||message.patch.view_revision>(current.view_revision??0)))
      parent.postMessage({type:'natural-data:ready'},location.origin);
     return;
    }
    relabelEncoding(next.chart.encoding,current.column_labels);
    accepted.current=next;
    setResult(next);
   }else if(message?.type!=='natural-data:tab'||message.result_ref!==ref||!accepted.current){
    return;
   }
   if(['chart','data','receipt'].includes(message.tab))selectTab(message.tab);
  };
  window.addEventListener('message',receive);parent.postMessage({type:'natural-data:ready'},location.origin);
  return()=>window.removeEventListener('message',receive);
 },[]);
 useEffect(()=>{
  if(!embedded||!result)return;
  const node=main.current!;
  let lastHeight=-1, lastRendered=false, frame=0;
  const report=()=>{
   const height=Math.ceil(node.getBoundingClientRect().height);
   const rendered=tab!=='chart'||!node.querySelector('[data-chart-loading]');
   if(height===lastHeight&&rendered===lastRendered)return;
   lastHeight=height;lastRendered=rendered;
   parent.postMessage({type:'natural-data:height',height,rendered,tab,result_ref:ref,view_revision:result.view_revision??0},location.origin);
  };
  const schedule=()=>{
   cancelAnimationFrame(frame);
   frame=requestAnimationFrame(report);
  };
  const resize=typeof ResizeObserver==='undefined'?null:new ResizeObserver(schedule);
  resize?.observe(node);
  const pivot=tab==='chart'&&Boolean(result.report||result.chart?.type==='pivot-table');
  const mutation=(pivot||!resize)&&typeof MutationObserver!=='undefined'?new MutationObserver(schedule):null;
  mutation?.observe(node,{childList:true,subtree:true,attributes:true,attributeFilter:['data-chart-loading','hidden']});
  if(!resize)window.addEventListener('resize',schedule);
  schedule();
  return()=>{resize?.disconnect();mutation?.disconnect();cancelAnimationFrame(frame);if(!resize)window.removeEventListener('resize',schedule);};
 },[result,tab]);
 useEffect(()=>{
  if(!embedded||parent===window)return;
  const sync=()=>{const host=window.frameElement?.parentElement||parent.document.documentElement,style=parent.getComputedStyle(host);for(const name of Array.from(style)){if(name.startsWith('--dsw-'))document.documentElement.style.setProperty(name,style.getPropertyValue(name));}document.documentElement.style.colorScheme=style.colorScheme;};
  sync();const observer=new MutationObserver(sync);for(let node=window.frameElement?.parentElement;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['class','data-theme','style']});return()=>observer.disconnect();
 },[]);
 const chartResult=useMemo(()=>monthlyDisplayResult(result,result?.chart?.type),[result]);
 const claims=useMemo(()=>result?scopedResultClaims(result,embedded):[],[result,embedded]);
 const forecasts=useMemo(()=>result?claims.map(claim=>forecastView(result,claim)).filter(view=>view!==null):[],[result,claims]);
 const {claimLimitations,answerLimitations}=useMemo(()=>result?scopedResultLimitations(result,claims,embedded,forecasts)
   :{claimLimitations:[],answerLimitations:[]},[result,claims,embedded,forecasts]);
 const unplottedClaims=useMemo(()=>{
  const plotted=new Set(forecasts.map(view=>view.id));
  return claims.filter(claim=>!plotted.has(claim?.claim_id)
    && !(forecasts.length&&claim?.operation==='matrix_method'&&claim.method_id==='matrix.0'));
 },[claims,forecasts]);
 if(!result){
  if(error)return <p role="alert">{error}</p>;
  if(embedded)return <span role="status" className="nw-sr-only">正在读取查询结果…</span>;
  return <main className="nw-result nw-skeleton-standalone" aria-busy="true"><div className="nw-skel-bar" style={{width:'42%',height:20}}/><div className="nw-skel-bar" style={{width:'24%',height:14,marginTop:10}}/><div className="nw-skel-bar" style={{width:'100%',height:220,marginTop:16}}/><span role="status" className="nw-sr-only">正在读取查询结果…</span></main>;
 }
 const keepChart=visited.includes('chart'),keepData=visited.includes('data');
 const tableChart=!result.report&&(!result.chart||result.chart.type==='table');
 const showChart=tab==='chart'||tab==='data'&&tableChart;
 const hideTableChart=tableChart&&!showChart;
 const hideData=tab!=='data';
 const sourceName=result.source_provenance?.synthetic?'':result.source_provenance?.name;
 return <main ref={main} data-document-id={documentId} className={"nw-result"+(embedded?" nw-embedded":"")}><header><h2>{resultDisplayTitle(result.title,result.source_provenance)}</h2><span>{result.status==='succeeded'?'查询完成':result.status}</span></header>
 <p className="nw-meta">{result.row_count||0} 行{sourceName?' · '+sourceName:''}{result.truncated?' · 已截断，统计仅覆盖当前返回部分':''}</p>
 {result.reason_code==='UNKNOWN'&&<p role="note">{unknownNote(result)}</p>}{result.reason_code==='NO_MATCH'&&<p role="note">当前授权范围及筛选条件下没有匹配记录。</p>}{error&&<p role="alert">{error}</p>}{result.error&&<p role="alert">{result.error}</p>}
 {keepChart&&forecasts.length>0&&<div hidden={tab!=='chart'}>{forecasts.map(view=><ForecastSection key={view.id} forecast={view}/>)}</div>}
 {result.status==='succeeded'&&(keepChart||tableChart&&keepData)&&<div className={tableChart?'nw-table-panel':undefined} data-table-tab={tableChart?tab:undefined} hidden={!showChart} aria-hidden={hideTableChart||undefined}>
  {result.report?<section aria-label="多维报表" className="nw-report-grid" style={{display:'grid',gridTemplateColumns:`repeat(${result.report.columns},minmax(0,1fr))`,gap:16}}>{result.report.cells.map((cell:any)=><section key={cell.id} aria-label={cell.title}><h3>{cell.title}</h3><ProductVisualization result={cell} visualization={cell.chart} runId={ref+'-'+cell.id}/><p>{cell.note}</p></section>)}</section>
   :trainingChartCollapsed(result,forecasts.length)&&tab==='chart'?<details className="nw-forecast-training">
      <summary>查询明细表格（点击展开）</summary>
      <ProductVisualization result={result} visualization={DATA_VISUALIZATION} runId={ref+'-training-table'}/>
    </details>
   :<ProductVisualization result={chartResult} visualization={result.chart} runId={ref}/>}
 </div>}
 {result.status==='succeeded'&&keepData&&!tableChart&&<div className="nw-table-panel" hidden={hideData} aria-hidden={hideData||undefined}>
  <ProductVisualization result={result} visualization={DATA_VISUALIZATION} runId={ref+'-data'}/>
 </div>}
 {result.status==='succeeded'&&(keepChart||keepData)&&<div hidden={tab==='receipt'}><AnswerBasis result={result} limitations={answerLimitations} /></div>}
 {result.status==='succeeded'&&keepChart&&<div hidden={tab!=='chart'}><AnalysisFacts result={result} claims={unplottedClaims} limitations={claimLimitations}/></div>}
 {(!embedded||visited.includes('receipt'))&&<div hidden={embedded&&tab!=='receipt'}><ResultReceipt result={result} claims={claims}/></div>}</main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
