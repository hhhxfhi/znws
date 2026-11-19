import {isExploratory} from './presentation-intent.js';

const unitSuffix=/\((件|条|份|次|个|户|家|人|万元|亿元|元|%)\)\s*$/;
const monthKey=/^([1-9]\d{3}-(?:0[1-9]|1[0-2]))(?:-01)?$/;
const yearHeading=/^([1-9]\d{3})(?:\s*年(?:度)?)?$/;
const location=/(?:^|[\s，、。；：#*（(])([\p{Script=Han}]{2,8}(?:自治区|省|市|县))(?=[\p{Script=Han}\s，、。；：）)]|$)/gu;
const clean=text=>String(text??'').normalize('NFKC').replaceAll('**','').trim();
const metricStem=text=>clean(text).replace(unitSuffix,'').replace(/(?:数量|数|量)$/,'').trim();
const numeric=text=>{
  const claim=clean(text);
  if(!/^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/.test(claim))return null;
  const value=Number(claim.replaceAll(',',''));
  return Number.isFinite(value)?{value,decimals:claim.split('.')[1]?.length??0}:null;
};
const pipeRow=line=>/^\s*\|[\s\S]*\|\s*$/.test(line)&&!line.includes('\\|')?line.split('|').slice(1,-1):null;

function monthNumber(text){
  return clean(text).match(/^(0?[1-9]|1[0-2])\s*(?:月(?:份)?)?$/)?.[1]?.padStart(2,'0');
}

function periodsForRows(columns,rows){
  const time=columns.flatMap((_,i)=>rows.every(row=>monthKey.test(clean(row[i])))?[i]:[]);
  if(time.length===1)return {dimensions:time,months:rows.map(row=>clean(row[time[0]]).match(monthKey)[1])};
  if(time.length)return null;
  const years=columns.flatMap((column,i)=>/^(?:年|年份|年度)$/.test(clean(column))?[i]:[]);
  const months=columns.flatMap((column,i)=>/^(?:月|月份|月度)$/.test(clean(column))?[i]:[]);
  if(years.length!==1||months.length!==1)return null;
  const [year]=years,[month]=months;
  const periods=rows.map(row=>{
    const y=clean(row[year]).match(yearHeading)?.[1],m=monthNumber(row[month]);
    return y&&m?y+'-'+m:null;
  });
  return periods.every(Boolean)?{dimensions:[year,month],months:periods}:null;
}

function completeTable(result){
  const {columns,rows,complete,row_count:previewCount}=result?.values??{};
  const lineage=result?.result_lineage;
  if(result?.status!=='succeeded'||!result.result_ref||result.truncated||isExploratory(result)
    ||lineage?.purpose!=='answer'||!lineage.request_ref||!lineage.session_id
    ||result.receipt?.read_only!==true||!result.receipt.query_hash||lineage.source_id!==result.receipt.source_id
    ||['conflict','auxiliary'].includes(result.validation?.state)||result.validation?.period_grain?.state==='auxiliary'
    ||complete!==true||!Array.isArray(columns)||columns.length<2||!Array.isArray(rows)
    ||!rows.length||rows.length!==result.row_count||rows.length!==previewCount
    ||rows.some(row=>!Array.isArray(row)||row.length!==columns.length))return null;
  return {columns,rows,lineage};
}

function resultColumns(result){
  const table=completeTable(result);
  if(!table)return [];
  const {columns,rows}=table;
  const periods=periodsForRows(columns,rows);
  if(!periods)return [];
  const {dimensions,months}=periods;
  if(new Set(months).size!==months.length)return [];
  const geography=String(result.title||'').matchAll(location);
  const places=new Set([...geography].map(match=>match[1]));
  return columns.flatMap((column,index)=>{
    const stem=metricStem(column);
    if(dimensions.includes(index)||stem.length<2||/^(?:数值|计数|结果|指标|数据|总计)$/.test(stem)
      ||!rows.some(row=>numeric(row[index]))||rows.some(row=>row[index]!==null&&!numeric(row[index])))return [];
    const values=new Map(months.map((month,i)=>[month,numeric(rows[i][index])?.value??null]));
    const unit=result.analysis_options?.column_units?.[column]||clean(column).match(unitSuffix)?.[1]||result.metric_contract?.unit;
    return [{column,stem,unit,values,places}];
  });
}

function yearPair(result,years){
  const table=completeTable(result);
  if(!table)return null;
  const [series,...others]=resultColumns(result);
  if(series&&!others.length){
    const periods=[...series.values.keys()];
    if(!periods.every(period=>years.includes(period.slice(0,4)))||
      years.some(year=>!periods.some(period=>period.startsWith(year))))return null;
    const months=[...new Set(periods.map(period=>period.slice(5)))];
    return {label:series.stem,unit:series.unit||'',left:series.column,lineage:table.lineage,
      byMonth:new Map(months.map(month=>[month,years.map(year=>series.values.get(year+'-'+month)??null)]))};
  }
  const monthColumns=table?.columns.flatMap((name,index)=>/^(?:月|月份|月度)$/.test(clean(name))?[index]:[]);
  if(monthColumns?.length!==1)return null;
  const {columns,rows,lineage}=table;
  const [monthIndex]=monthColumns;
  if(columns.some((_,i)=>i!==monthIndex&&rows.some(row=>row[i]!==null&&!numeric(row[i]))))return null;
  const dataMonths=rows.map(row=>monthNumber(row[monthIndex]));
  if(dataMonths.some(month=>!month)||new Set(dataMonths).size!==dataMonths.length)return null;
  const fields=years.map(year=>columns.flatMap((name,index)=>{
    const heading=clean(name).replace(unitSuffix,'');
    const before=heading.match(new RegExp('^'+year+'\\s*年[ _：:]*(.+)$'))?.[1];
    const after=heading.match(new RegExp('^(.+?)[ _（(]*'+year+'\\s*年?[）)]?$'))?.[1];
    const label=metricStem(before||after||'');
    const unit=result.analysis_options?.column_units?.[name]||clean(name).match(unitSuffix)?.[1]||'';
    return label.length>=2?[{index,label,unit}]:[];
  }));
  if(fields.some(field=>field.length!==1))return null;
  const [left,right]=fields.map(([item])=>item);
  if(left.index===right.index||left.label!==right.label||left.unit!==right.unit)return null;
  return {label:left.label,unit:left.unit,left:columns[left.index],right:columns[right.index],
    lineage, byMonth:new Map(dataMonths.map((month,index)=>[month,
      [numeric(rows[index][left.index])?.value??null,numeric(rows[index][right.index])?.value??null]]))};
}

export function verifiedYearComparisonTable(question,evidence,requestRef,sessionId){
  const text=clean(question);
  const years=[...new Set([...text.matchAll(/((?:19|20)\d{2})\s*年/g)].map(match=>match[1]))].sort();
  if(years.length!==2||!/按月|每月|月份|逐月/.test(text)||!/比较|对比/.test(text)
    ||!/并列|分列|两列|列.*表格/.test(text)||!/1\s*月\s*(?:至|到|[-—])\s*12\s*月|全年/.test(text)
    ||/同比|环比|预测|差额|涨幅|增长率|最高|最低|汇总合计|分别统计|另外|顺便|(?:按|分)(?:地区|地市|区县|类别|单位)/.test(text))return null;
  const results=evidence.filter(result=>result?.status==='succeeded'&&result?.result_ref&&!isExploratory(result));
  if(!results.length)return null;
  const matches=results.map(result=>yearPair(result,years));
  if(matches.some(value=>!value||value.lineage.request_ref!==requestRef||value.lineage.session_id!==sessionId))return null;
  const first=matches[0];
  if(!text.includes(first.label)||first.byMonth.size<2)return null;
  if(matches.some(item=>item.label!==first.label||item.unit!==first.unit
    ||item.lineage.source_id!==first.lineage.source_id
    ||item.lineage.config_fingerprint!==first.lineage.config_fingerprint
    ||item.lineage.population_fingerprint!==first.lineage.population_fingerprint
    ||item.byMonth.size!==first.byMonth.size||[...item.byMonth].some(([month,values])=>
      !first.byMonth.has(month)||values.some((value,index)=>value!==first.byMonth.get(month)[index]))))return null;
  if(!first.lineage.population_fingerprint||!first.lineage.config_fingerprint)return null;
  const title=first.left.replace(/[_ ]*(?:19|20)\d{2}\s*年?/,'').replace(unitSuffix,'').trim();
  if(!title||!/^[\p{Script=Han}\p{L}\p{N} ·（）()_-]+$/u.test(title))return null;
  const heading='月份'+(first.unit?'（'+first.unit+'）':'');
  const lines=[`**${years[0]} 年和 ${years[1]} 年${title}按月对比**`,'',
    `| ${heading} | ${years[0]} 年 | ${years[1]} 年 |`,'| --- | ---: | ---: |'];
  for(let month=1;month<=12;month++){
    const values=first.byMonth.get(String(month).padStart(2,'0'))||[null,null];
    lines.push(`| ${month}月 | ${values.map(value=>value===null?'—':String(value)).join(' | ')} |`);
  }
  if(lines.some(line=>line.includes('| —')))lines.push('','— 表示本次未取得对应数值。');
  return lines.join('\n');
}

function tableChanges(text,evidence){
  const candidates=evidence.flatMap(resultColumns);
  if(!candidates.length)return [];
  const lines=String(text).split('\n'),changes=[];
  let fence=null,context=[];
  for(let line=0;line<lines.length;line++){
    const marker=lines[line].match(/^\s*(`{3,}|~{3,})/)?.[1]?.[0];
    if(marker){fence=fence===marker?null:fence||marker;context=[];continue;}
    if(fence||/^\s*>/.test(lines[line]))continue;
    const header=pipeRow(lines[line]),separator=pipeRow(lines[line+1]||'');
    if(!header||separator?.length!==header.length||!separator.every(cell=>/^:?-{2,}:?$/.test(cell.trim()))){
      if(lines[line].trim())context=[...context.slice(-3),lines[line]];
      continue;
    }
    const heading=clean(header[0]),unit=heading.match(unitSuffix)?.[1];
    const years=header.slice(1).map(cell=>clean(cell).match(yearHeading)?.[1]);
    let last=line+2;
    while(last<lines.length&&pipeRow(lines[last]))last++;
    const describe=[heading,...context.slice().reverse()];
    const anchor=describe.find(part=>candidates.some(candidate=>clean(part).includes(candidate.stem)));
    const scopeText=[heading,context.at(-1),anchor].filter(Boolean).join(' ');
    const places=[...scopeText.matchAll(location)].map(match=>match[1]);
    const bound=anchor?candidates.filter(item=>clean(anchor).includes(item.stem)
      &&(!unit||!item.unit||item.unit===unit)
      &&(!places.length||places.every(place=>item.places.has(place)))):[];
    if(years.length>=2&&years.every(Boolean)&&new Set(years).size===years.length&&bound.length===1
      &&(/(?:月|同月对比)/.test(heading)||heading.includes(bound[0].stem))){
      const [result]=bound, edits=[],matched=new Set();
      for(let index=line+2;index<last;index++){
        const row=pipeRow(lines[index]),month=monthNumber(row[0]);
        if(!month||row.length!==header.length)continue;
        for(const [i,year] of years.entries()){
          const claimed=numeric(row[i+1]);
          if(!claimed)continue;
          const period=year+'-'+month,actual=result.values.get(period);
          if(actual===null||actual===undefined){
            edits.push({line:index,column:i+1,period,metric:result.column,claimed:claimed.value,actual:null,unit:result.unit});
            continue;
          }
          if(Math.abs(claimed.value-actual)>.5*10**-claimed.decimals+1e-9){
            edits.push({line:index,column:i+1,period,metric:result.column,claimed:claimed.value,actual,unit:result.unit});
          }else matched.add(i+1);
        }
      }
      // A matching observation in each edited year helps distinguish a
      // displaced value from a separate population with the same metric name.
      changes.push(...edits.filter(edit=>matched.has(edit.column)));
    }
    line=last-1;
    context=[];
  }
  return changes;
}

export function prosePeriodTableIssues(text,evidence){
  return tableChanges(text,evidence).slice(0,4).map(({period,metric,claimed,actual,unit})=>
    actual===null?'正文对比表缺少 '+period+'「'+metric+'」的已核实值。保留其他月份，这一格标为未核实。'
      :'正文对比表与同轮回执不一致，'+period+'「'+metric+'」回执为 '+actual+(unit?' '+unit:'')
        +'，正文写 '+claimed+'。按回执修正这一格，无需重查。');
}

export function repairProsePeriodTable(text,evidence){
  const changes=tableChanges(text,evidence);
  if(!changes.length)return text;
  const lines=String(text).split('\n');
  for(const {line,column,actual} of changes){
    const fields=lines[line].split('|');
    fields[column+1]=fields[column+1].replace(/[+-]?[\d０-９][\d０-９,，.．]*/,actual===null?'未核实':String(actual));
    lines[line]=fields.join('|');
  }
  return lines.join('\n');
}
