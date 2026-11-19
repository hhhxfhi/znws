import {isExploratory} from './presentation-intent.js';
import {activeEvidence} from './result-lineage.js';

const dateParts=/^(\d{4})-(\d{2})-(\d{2})$/;
const validDate=day=>typeof day==='string'&&dateParts.test(day)&&(()=>{
  const parsed=new Date(day+'T00:00:00.000Z');
  return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===day;
})();
const maxIntent=/哪(?:个|种|类|一|些)[^。！？?!]{0,65}(?:最(?:多|高)|(?:排(?:在|名)?|位列|位居)(?:第[一1]|首位)|居首)[。！？?!\s]*$/;
const minIntent=/哪(?:个|种|类|一|些)[^。！？?!]{0,65}最(?:少|低)[。！？?!\s]*$/;
const additional=/(?:另外|同时|分别|并且|以及|占比|比例|趋势|环比|同比|比较|对比|为什么|差多少|多少倍|第二名|第[2-9]名|前[一二两三四五六七八九十\d]|[；;\n]|[？?].*\S)/;
const countUnit=/^(?:条|件|个|家|笔|人|项|张|份|宗|批|次|本|台|辆|户)$/;
const coverageQuestion=/[,。;]\s*(?:再|并)?\s*(?:请)?\s*(?:说明|告诉我)?\s*(?:数据|记录|观测)(?:的)?(?:截至|截止(?:到)?)\s*(?:哪(?:一)?天|哪(?:一)?日|什么日期|何时)[。!?\s]*$/;
const breakdownQuestion=/^([^。?!;,]+?)(?:按|按照|依照)\s*([\p{Script=Han}]{2,20}?)(?:分别|各自|各)?(?:有|是|为)?多少(?:件|条|笔|宗|份|批|次|家|个)?\s*$/u;
const rankingQuestion=/^(?:(?:数量|件数|记录数|条数|笔数)\s*)?(最[多少高低])的?(?:是)?哪(?:一)?(?:个|类|种|项)\s*([\p{Script=Han}]{2,20})[。!?\s]*$/u;
const cumulativeCount=/^(?:请\s*)?(?:帮我\s*)?(?:统计|查询|查一下|计算|汇总|查看)?\s*(?:本年度?|今年|当年度?|(?:19|20)\d{2}\s*年(?:度)?)\s*[^，。！？?!；;\n]{2,65}(?:件数|条数|记录数|案卷数|次数|数量)(?:是多少|有多少)?[。！？?!\s]*$/;

function intent(question){
  const text=question.normalize('NFKC').trim().replace(coverageQuestion,'');
  const parts=text.split(/[?。]/).map(part=>part.trim()).filter(Boolean);
  if(parts.length===2){
    const breakdown=breakdownQuestion.exec(parts[0]),ranking=rankingQuestion.exec(parts[1]);
    if(breakdown&&ranking&&!additional.test(breakdown[1])&&breakdown[2].endsWith(ranking[2])){
      return {order:/多|高/.test(ranking[1])?'最多':'最少',axis:breakdown[2]};
    }
  }
  if(additional.test(text))return null;
  if(maxIntent.test(text)&&!/最(?:少|低)/.test(text))return {order:'最多'};
  if(minIntent.test(text)&&!/最(?:多|高)/.test(text))return {order:'最少'};
  return null;
}

export function verifiedCancellationAnswer(question,evidence,requestRef,sessionId){
  if(!requestRef||!sessionId||!question.includes('注销'))return null;
  const requested=intent(question);
  if(!requested)return null;
  const formal=activeEvidence(evidence).filter(result=>result.status==='succeeded'&&result.result_ref
    &&result.result_lineage?.purpose==='answer'&&!isExploratory(result));
  if(formal.length!==1)return null;
  const [result]=formal;
  const {answer_contract:contract,result_lineage:lineage,receipt,values}=result;
  const proof=contract?.cancellation_object;
  if(proof?.schema_version!==1||!['completed','application'].includes(proof.mode)
    ||contract.result_ref!==result.result_ref||!contract.complete||result.truncated
    ||lineage.request_ref!==requestRef||lineage.session_id!==sessionId
    ||lineage.source_id!==receipt?.source_id||receipt?.read_only!==true||!receipt.query_hash
    ||result.validation?.state==='conflict'||values?.complete!==true
    ||!Array.isArray(values.rows)||!Array.isArray(values.columns)
    ||values.rows.length!==result.row_count||!values.rows.length||values.columns.length!==2
    ||values.rows.some(row=>!Array.isArray(row)||row.length!==2))return null;
  const specialty=values.columns.indexOf(proof.columns?.specialty),count=values.columns.indexOf(proof.columns?.count);
  if(specialty<0||count<0||specialty===count)return null;
  const groups=values.rows.map(row=>({label:row[specialty],count:row[count]}));
  if(groups.some(row=>typeof row.label!=='string'||!row.label.trim()||!Number.isSafeInteger(row.count)||row.count<0)
    ||new Set(groups.map(row=>row.label)).size!==groups.length)return null;
  const best=groups.reduce((value,row)=>requested.order==='最多'?Math.max(value,row.count):Math.min(value,row.count),groups[0].count);
  const labels=groups.filter(row=>row.count===best).map(row=>row.label).join('、');
  const period=Number.isSafeInteger(proof.year)?proof.year+' 年':'本次查询中';
  const subject=proof.mode==='completed'?'实际注销件数':'申请注销案卷数';
  const summary=period+subject+requested.order+'的专业是'+labels+'，'+best.toLocaleString('zh-CN')+' 件。';
  if(proof.mode==='application'&&!proof.requested_application)
    return summary+' 实际注销量需要对应的注销生效状态和日期。';
  return summary;
}

function boundRanking(question,evidence){
  const requested=intent(question),total=cumulativeCount.test(question.normalize('NFKC').trim())
    &&/(?:累计|累积|合计|总量|总数)/.test(question)&&!additional.test(question);
  if(!requested&&!total)return null;
  const formal=activeEvidence(evidence).filter(result=>result?.status==='succeeded'&&result.result_ref
    &&!isExploratory(result)&&result.validation?.period_grain?.state!=='auxiliary');
  if(formal.length!==1)return null;
  const [result]=formal;
  const {answer_contract:contract,business_project:publication,values,result_lineage:lineage}=result;
  const proof=contract?.ranking_observation;
  const scope=result.validation?.request_scope?.request;
  const observed=proof?.observed_through;
  if(result.receipt?.read_only!==true||result.validation?.state==='conflict'||result.truncated
    ||contract?.kind!=='sql_aggregate'||contract.complete!==true||contract.result_ref!==result.result_ref
    ||proof?.schema_version!==1||proof.result_ref!==result.result_ref
    ||!proof.request_ref||lineage?.request_ref!==proof.request_ref||scope?.user_ref!==proof.request_ref||scope.quote!==question
    ||lineage.purpose!=='answer'||!lineage.session_id||!proof.source_id
    ||lineage.source_id!==proof.source_id||result.receipt.source_id!==proof.source_id
    ||!publication?.id||!publication.version||publication.id!==proof.publication?.id||publication.version!==proof.publication?.version
    ||![observed,proof.start_inclusive,proof.end_exclusive].every(validDate)
    ||observed<proof.start_inclusive||observed>=proof.end_exclusive
    ||Boolean(total)!==(proof.shape==='total')
    ||requested?.axis&&proof.category_column?.normalize('NFKC').trim()!==requested.axis)return null;
  return {result,proof,values,total,...requested};
}

function displayDay(day){
  const [,year,month,date]=dateParts.exec(day);
  return year+' 年 '+Number(month)+' 月 '+Number(date)+' 日';
}

function observationSentence(proof){
  const dateLabel=/^\p{Script=Han}{2,20}$/u.test(proof.date_label||'')?proof.date_label:'记录日期';
  return '本次统计范围内，最晚'+dateLabel+'为 '+displayDay(proof.observed_through)+'。';
}

const cutoffMarker=/(?:最晚|最近|最新)(?:的)?(?:[^\d。，,]{0,12}(?:日期|时间|记录|日))?\s*(?:为|是|截至|到|至)?|(?:数据|记录|观测|报送|实际)[^\d。，,]{0,12}(?:截至|截止(?:到)?|覆盖(?:到|至)|更新(?:到|至))/g;
const cutoffDate=/^\s*(?:[：:（(]\s*)?(?:(\d{4})\s*(?:年\s*|[-/]))?(\d{1,2})\s*(?:月\s*|[-/])(\d{1,2})\s*[日号]?(?!\d)/;
const completeCoverage=/(?:数据|记录|观测|报送)[^。，,]{0,16}(?:覆盖|涵盖)[^。，,]{0,10}(?:全年|完整年度|整个年度)|(?:已|完整|全部)(?:完整|全部)?(?:覆盖|涵盖)[^。，,]{0,10}(?:全年|完整年度|整个年度)|(?:全年|整个年度|完整年度)[^。，,]{0,16}(?:均有|都有|完整覆盖|已全部报送)/;

export function rankingObservationIssues(text,evidence,question){
  const ranking=boundRanking(question,evidence);
  if(!ranking)return [];
  const {proof}=ranking;
  const observed=proof.observed_through;
  const clauses=text.replaceAll('**','').split(/[。，,；;\n]/);
  const dates=clauses.flatMap(clause=>[...clause.matchAll(cutoffMarker)].flatMap(match=>{
    const date=cutoffDate.exec(clause.slice(match.index+match[0].length));
    if(!date)return [];
    const [,year,month,day]=date;
    return [[year||observed.slice(0,4),month.padStart(2,'0'),day.padStart(2,'0')].join('-')];
  }));
  if(clauses.some(clause=>completeCoverage.test(clause)&&!/(?:未|不|无法|不能|尚待).{0,11}(?:覆盖|核实|确认|报送|有)/.test(clause)))
    return [observationSentence(proof)+'以实际记录日期说明统计结果。年度筛选范围无法证明全年完整覆盖。'];
  if(dates.includes(observed)&&dates.every(day=>day===observed))return [];
  return [observationSentence(proof)+'用已经核实的最晚业务日期说明排名覆盖范围，无需重查。'];
}

export function verifiedRankingAnswer(question,evidence,requestRef,sessionId){
  const ranking=boundRanking(question,evidence);
  if(!ranking)return null;
  const {result,proof,values,order,axis,total:scalar}=ranking;
  const dateColumns=proof.date_aggregate_columns||[];
  if(!requestRef||!sessionId||proof.request_ref!==requestRef||result.result_lineage.session_id!==sessionId
    ||values?.complete!==true||values.row_count!==result.row_count||!Array.isArray(values.columns)||!Array.isArray(dateColumns)
    ||values.columns.length!==(scalar?1:2)+dateColumns.length||dateColumns.some(name=>!values.columns.includes(name))
    ||!Array.isArray(values.rows)||values.rows.length!==result.row_count||!values.rows.length)return null;
  const category=values.columns.indexOf(proof.category_column),count=values.columns.indexOf(proof.count_column);
  const unit=result.analysis_options?.column_units?.[proof.count_column];
  if(scalar){
    const number=values.rows[0]?.[0];
    if(count!==0||values.rows.length!==1||values.rows[0]?.length!==1||!Number.isSafeInteger(number)||number<1
      ||!countUnit.test(unit||''))return null;
    return proof.start_inclusive.slice(0,4)+' 年已查到 **'+number.toLocaleString('zh-CN')+' '+unit+'**。\n\n'
      +observationSentence(proof);
  }
  if(category<0||count<0||category===count||!countUnit.test(unit||''))return null;
  const labels=new Set();
  for(const row of values.rows){
    const label=typeof row?.[category]==='string'?row[category].normalize('NFKC').trim():'';
    if(!label||label.length>80||/[\p{Cc}*_|<>`\\]/u.test(label)||labels.has(label)
      ||!Number.isSafeInteger(row[count])||row[count]<1)return null;
    labels.add(label);
  }
  const numbers=values.rows.map(row=>row[count]),total=numbers.reduce((sum,n)=>sum+n,0);
  if(!Number.isSafeInteger(total))return null;
  const extreme=order==='最多'?Math.max(...numbers):Math.min(...numbers);
  const leaders=values.rows.filter(row=>row[count]===extreme).map(row=>'**'+row[category].trim()+'**');
  const breakdown=axis?'\n\n| '+axis+' | 数量（'+unit+'） |\n| --- | ---: |\n'
    +values.rows.map(row=>'| '+row[category].trim()+' | '+row[count]+' |').join('\n'):'';
  const end=new Date(proof.end_exclusive+'T00:00:00Z');
  end.setUTCDate(end.getUTCDate()-1);
  const period=proof.period_grain==='month'?proof.start_inclusive.slice(0,7)+' 至 '+end.toISOString().slice(0,7)+' '
    :proof.start_inclusive.slice(0,4)+' 年';
  return period+'已查到的记录中，'+leaders.join('、')+(leaders.length>1?'并列':'')+order+(leaders.length>1?'，各有 ':'，有 ')+extreme+' '+unit+'。'
    +'本次统计共 '+total+' '+unit+'。\n\n'+observationSentence(proof)+breakdown;
}
