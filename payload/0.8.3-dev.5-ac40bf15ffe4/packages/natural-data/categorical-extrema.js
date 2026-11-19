// Compare named category extrema only against complete, unambiguous tables.
// Supports wide metrics and a metric faceted by one categorical dimension.
import {isExploratory} from './presentation-intent.js';

const codeCharacter=/[A-Za-z0-9_-]/;
const choiceLimits=[
  /(?:前\s*(?:[1-9]\d*|[一二两三四五六七八九十百]+)\s*(?:个(?!月|年|季度|周)|名|项|类|位|条)|\btop\s*-?\s*[1-9]\d*\b)/i,
  /(?:只|仅)(?:需|要)?\s*(?:列出?|给出?|说|看|举出?|取|选出?|报|返回|展示|提供)?[^，,。；;\n]{0,10}第\s*(?:一|1)\s*(?:名|项|位)/u,
  /(?:只(?:需|要)?|仅(?:需|要)?|任选|任取|任意|各)\s*(?:(?:列出?|给出?|看|举出?|取|选出?|报|返回|展示|提供)\s*)?(?:一|1)\s*(?:个(?!月|年|季度|周|小时|地区|图|表)|名|项|类|种|例|位|条|组)/u,
  /(?:给出?|列出?|举出?|选出?)\s*(?:一|1)\s*(?:例|个(?:例子|案例|样例|类别|分类|最(?:多|少|高|低)的))/u,
];
const allTies=/(?:所有|全部|完整|都要|一并列出).{0,14}(?:并列|类别|分类|极值)|并列.{0,14}(?:全部|都列出|完整列出)|(?:都|分别|悉数)(?:列出|给出|展示)/;
const categorySeparator=/^(?:[\s、，,\/／和与及暨“”‘’「」"']|以及|[（(][^）)]{0,18}[）)])*$/u;
const afterListing=/^\s*(?:的)?\s*(?:类别|分类|领域|对象|项目|单位)?\s*(?:(?:分别)?(?:是|为|有|包括)|[:：]|例如|比如)/u;
const sameRankAfter=/^[^，,]{0,32}[，,][^，,]{0,40}(?:同为|同样|同列|同居|并列|持平)/u;
const moreAfterTie=/^[^，,]{0,24}[，,]\s*(?:另外|此外)?\s*(?:还有|另有|以及)/u;
const anotherRank=/(?:[，,、]\s*)?(?:其次|其后|次之|紧随|随后|排名第|第[二三四五六七八九十](?:名|位)|最(?:多|少|高|低))/u;
const memberCount=String.raw`(\d+|[零一二两三四五六七八九十]{1,3})\s*(?:个(?!月|年|季度|周|小时)|项|类|种|组|名|位)`;
const afterExtremum=new RegExp(String.raw`^\s*(?:的)?\s*(?:[\p{L}]{1,18}?)?\s*(?:一?共(?:有|计)?|合计|有|为|是)\s*${memberCount}(?:[\p{L}]{1,18}?)?\s*[,、]?\s*并列\s*(?:第\s*(?:一|1)(?:名|位)?|最[多高少低])`,'u');
const beforeExtremum=new RegExp(String.raw`(?<![\d.零一二两三四五六七八九十])${memberCount}(?:[\p{L}]{1,18}?)?\s*[,、]?\s*并列\s*$`,'u');
const subsetCount=/(?:其中|仅|只|例如|举例|列出|展示|选取|提供|给出|任选|任取)\s*$/u;
const allCount=/(?:一共|共有|共计|合计|全部|总计|总数)/u;

function cardinality(raw){
  if(/^\d+$/.test(raw))return Number(raw);
  const digit=value=>'零一二三四五六七八九'.indexOf(value.replace('两','二'));
  if(raw.length===1&&raw!=='十')return digit(raw);
  const ten=/^([一二两三四五六七八九])?十([一二三四五六七八九])?$/.exec(raw);
  return ten?(ten[1]?digit(ten[1]):1)*10+(ten[2]?digit(ten[2]):0):null;
}

function tieCardinalityIssue(sentence,match,series,userText){
  const text=sentence.normalize('NFKC');
  const before=sentence.slice(0,match.index).normalize('NFKC');
  const after=sentence.slice(match.index+match[0].length).normalize('NFKC');
  const trailing=afterExtremum.exec(after),leading=beforeExtremum.exec(before);
  if(trailing?.[0].includes('并列第')&&/少|低/.test(match[0]))return null;
  const raw=trailing?.[1]??leading?.[1];
  if(!raw)return null;
  const count=cardinality(raw);
  if(!Number.isSafeInteger(count))return null;
  const marked=series.filter(item=>item.marker&&before.includes(item.marker));
  const [selected,...others]=marked.length?marked:series;
  if(others.length||!selected?.cardinalityProof)return null;
  const category=String(selected.category).normalize('NFKC');
  if(!category||!text.includes(category)&&!String(userText).normalize('NFKC').includes(category))return null;
  const context=trailing?before:before.slice(0,leading.index);
  const claimsTotal=allCount.test(trailing?trailing[0]:context.slice(-8));
  if(!claimsTotal&&subsetCount.test(context))return null;
  const values=selected.rows.map(row=>row[1]);
  const extreme=/多|高/.test(match[0])?Math.max(...values):Math.min(...values);
  const actual=values.filter(value=>value===extreme).length;
  if(count===actual||count<actual&&!claimsTotal&&userLimitedExtrema(userText,match[0]))return null;
  return '并列数量与完整结果不一致：'+selected.marker+'并列'+match[0]+'的类别有 '+actual+' 个。按已查询结果修正文案，无需重查。';
}

function userLimitedExtrema(userText, extreme){
  const clauses=String(userText).normalize('NFKC').split(/[。；;！？!?\n]|另外|同时|并且|并请|再(?:查|列出|给出)/).filter(part=>part.trim());
  if(clauses.some(part=>!(/最(?:多|少|高|低)/.test(part))&&allTies.test(part)&&part.includes('并列')))return false;
  const relevant=clauses.filter(part=>part.includes(extreme));
  const requests=relevant.length?relevant:clauses.length===1?clauses:[];
  return requests.length>0&&requests.every(part=>!allTies.test(part)&&choiceLimits.some(pattern=>pattern.test(part)));
}

function namedCategories(text, rows){
  const matches=[];
  for(const [name,value] of rows){
    for(let index=text.indexOf(name);index>=0;index=text.indexOf(name,index+name.length)){
      const end=index+name.length;
      if(codeCharacter.test(name[0])&&codeCharacter.test(text[index-1]||''))continue;
      if(codeCharacter.test(name.at(-1))&&codeCharacter.test(text[end]||''))continue;
      matches.push({name,value,index,end});
    }
  }
  matches.sort((a,b)=>a.index-b.index||b.name.length-a.name.length);
  let lastEnd=-1;
  return matches.filter(match=>{
    if(match.index<lastEnd)return false;
    lastEnd=match.end;
    return true;
  });
}

function claimedCategories(sentence, match, rows){
  const before=sentence.slice(0,match.index);
  const tailStart=match.index+match[0].length;
  const tail=sentence.slice(tailStart);
  const following=tail.split(anotherRank,1)[0];
  const mentions=namedCategories(sentence,rows);
  if(mentions.some(item=>item.index<=match.index&&item.end>=tailStart))return {};
  const left=mentions.filter(item=>item.end<=match.index);
  const last=left.at(-1);
  const leading=[];
  if(last&&before.length-last.end<=24){
    leading.unshift(last);
    for(let i=left.length-2;i>=0&&categorySeparator.test(sentence.slice(left[i].end,leading[0].index));i--){
      leading.unshift(left[i]);
    }
  }
  const namesAfter=afterListing.test(following)||sameRankAfter.test(following)
    ||before.trimEnd().endsWith('并列')&&moreAfterTie.test(following);
  const trailing=namesAfter?mentions.filter(item=>item.index>=tailStart&&item.end<=tailStart+following.length):[];
  const named=leading.at(-1)||trailing[0];
  return {named,claimed:[...leading,...trailing]};
}

export function categoricalExtremaIssues(text,evidence,userText=''){
  const series=[];
  for(const r of evidence){
    const v=r.values;
    // A receipt without a column header cannot name categories; skip only it.
    if(isExploratory(r)||r.status&&r.status!=='succeeded'||r.truncated||!v?.complete||!Array.isArray(v.columns)||!Array.isArray(v.rows)||!v.rows.length||!v.rows.every(Array.isArray))continue;
    const numeric=v.columns.flatMap((name,i)=>v.rows.every(row=>typeof row[i]==='number'&&Number.isFinite(row[i]))?[i]:[]);
    const categories=v.columns.flatMap((name,i)=>v.rows.every(row=>typeof row[i]==='string'&&row[i]&&!/^\d{4}[-/年]\d{1,2}|^\d{1,2}月$/.test(row[i]))?[i]:[]);
    if(!numeric.length||categories.length<1||categories.length>2)continue;
    const cardinalityProof=r.status==='succeeded'&&r.result_ref&&r.receipt?.read_only===true
      &&r.validation?.state!=='conflict'&&v.columns.length===2&&numeric.length===1&&categories.length===1
      &&v.row_count===v.rows.length&&r.row_count===v.rows.length&&v.rows.every(row=>row.length===2);
    for(const metric of numeric)for(const category of categories){
      const facet=categories.find(i=>i!==category),groups=new Map();
      for(const row of v.rows){
        const marker=facet===undefined?String(v.columns[metric]):row[facet];
        const rows=groups.get(marker)||[];rows.push([row[category],row[metric]]);groups.set(marker,rows);
      }
      for(const [marker,rows]of groups)if(rows.length>1&&new Set(rows.map(r=>r[0])).size===rows.length)
        series.push({marker,rows,category:v.columns[category],cardinalityProof,
          faceted:facet!==undefined,singleMetric:numeric.length===1&&facet===undefined});
    }
  }
  const issues=new Set();
  for(const sentence of String(text).replaceAll('**','').split(/[。；;\n]/))for(const m of sentence.matchAll(/最(?:多|少|高|低)/g)){
    const before=sentence.slice(0,m.index);
    if(/不是|并非|不能|不应|误称/.test(before.slice(-35)))continue;
    const countIssue=tieCardinalityIssue(sentence,m,series,userText);
    if(countIssue)issues.add(countIssue);
    const candidates=[];
    for(const s of series){
      const {named,claimed}=claimedCategories(sentence,m,s.rows);
      if(!named)continue;
      const marker=sentence.slice(0,Math.min(named.index,m.index)).lastIndexOf(s.marker);
      if(s.faceted?(marker<0||marker>=named.index):(!s.singleMetric&&marker<0))continue;
      candidates.push({s,named,marker,claimed});
    }
    if(!candidates.length)continue;
    const nearest=Math.max(...candidates.map(c=>c.marker));
    const selected=candidates.filter(c=>c.marker===nearest);
    // Different complete receipts can have different periods or populations.
    // A label alone cannot identify which one the sentence intends.
    if(new Set(selected.map(c=>JSON.stringify(c.s.rows))).size!==1)continue;
    const {s,named,claimed}=selected[0],values=s.rows.map(r=>r[1]);
    const expected=/多|高/.test(m[0])?Math.max(...values):Math.min(...values);
    const limited=userLimitedExtrema(userText,m[0]);
    const conflicting=(limited?[named]:claimed).find(item=>Math.abs(item.value-expected)>.011);
    if(conflicting){
      const correct=s.rows.filter(r=>Math.abs(r[1]-expected)<.011).map(r=>r[0]).join('、');
      issues.add('分类极值与完整结果不一致：'+s.marker+'中'+m[0]+'的是'+correct+'（'+expected+'），不能说成'+conflicting.name+'。按已查询结果修正文案，无需重查。');
    }else if(named.value===expected&&!limited){
      const names=new Set(claimed.map(item=>item.name));
      const missing=s.rows.filter(([name,value])=>value===expected&&!names.has(name)).map(([name])=>name);
      if(missing.length){
        issues.add('分类极值遗漏并列项：'+s.marker+'中并列'+m[0]+'（'+expected+'）的还有'+missing.join('、')+'。按已查询结果补齐并列项，无需重查。');
      }
    }
  }
  return [...issues].slice(0,4);
}
