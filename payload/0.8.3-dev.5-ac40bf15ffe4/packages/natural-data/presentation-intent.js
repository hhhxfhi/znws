// A card can show one chart and its native data tab.
const charts={
  'percent-stacked-bar':'(?:100\\s*%|百分比|百分之百)\\s*堆叠(?:柱状|柱形|条形)?图',
  'stacked-bar':'堆叠(?:柱状|柱形|条形)?图',
  'grouped-bar':'分组(?:柱状|柱形|条形)?图',
  bar:'横向(?:柱状|柱形)图|(?:横向)?条形图',
  column:'(?:竖向|纵向|垂直)?(?:柱状|柱形)图',
  line:'折线图|趋势线|折线',area:'面积图',pie:'饼(?:状|形)?图',donut:'环形图|圆环图',
  scatter:'散点图',bubble:'气泡图',heatmap:'热力图',treemap:'树状图|树图',
  histogram:'直方图',radar:'雷达图',funnel:'漏斗图',gauge:'仪表盘',waterfall:'瀑布图',
  boxplot:'箱线图',kpi:'指标卡','pivot-table':'多维透视表|透视表|交叉表'
};
const table='(?:表格|数据表|统计表|汇总表|明细表)';
const tableName=new RegExp('^'+table+'$');
const terms=new RegExp([table,...Object.values(charts)].join('|'),'gu');
const grain='(?:(?:逐|按)(?:月|季|年)(?:度)?)?';
const join=new RegExp('^\\s*(?:和|与|以及|及|、)\\s*'+grain+'\\s*$');
const also=new RegExp(
  '^(?:\\s*(?:显示|展示|呈现|列出))?(?:[，,]\\s*[^，,。；;\\n]{1,50})?[，,。；;\\n]?\\s*'+
  '(?:并且?|同时|也|再(?=把|将))\\s*(?:(?:把|将)[^，,。；;\\n]{0,28}?)?'+
  '(?:列成|列为|整理成|给出|显示|展示|提供|输出|画|用|以|附上)?\\s*'+grain+'\\s*$'
);
const rejectBefore=/(?:不要|不用|无需|无须|不需要|不必|只(?:要|用|给)|仅(?:要|用|给)|(?:改|换)(?:为|成|用))\s*(?:(?:给出|使用|绘制|展示|显示|输出|生成|做|画|用|以)\s*)?$/;
const rejectAfter=/^\s*(?:就|都)?\s*(?:不要(?:了)?|不用了|不需要了|即可|就行|就好)(?:[，,。；;！!？?\s]|$)/;
const chartKinds=Object.entries(charts).map(([kind,pattern])=>[kind,new RegExp('^(?:'+pattern+')$','u')]);
const kindOf=term=>tableName.test(term)?'table':chartKinds.find(([,pattern])=>pattern.test(term))?.[0];

export function chartOnlyKind(question){
  const text=String(question||'').normalize('NFKC').trim();
  const match=/^(?:(?:请帮我|帮我|请)\s*)?(?:(?:把|将)\s*(?:刚才|上轮|上一轮|上面|上述)的?(?:结果|图表|图)\s*)?(?:(?:还是|也|就)\s*)?(?:用|以|改成|改为|换成|换为|切换到)\s*(.{1,30}?)(?:的形式)?\s*(?:展示|显示|输出|呈现|就行)?\s*[。！!]?$/u.exec(text);
  return match?kindOf(match[1].trim())||null:null;
}

export function explicitChartType(text=''){
  const joint=jointChartType(text);
  if(joint)return joint;
  let s=String(text).normalize('NFKC');
  const change=[...s.matchAll(/(?:改|换)(?:成|为|用)|(?:只|仅)(?:要|用|给)/g)].at(-1);
  if(change&&[...s.slice(change.index+change[0].length).matchAll(terms)].length)s=s.slice(change.index+change[0].length);
  const kinds=new Set([...s.matchAll(terms)].filter(term=>
    !rejectBefore.test(s.slice(0,term.index))&&!rejectAfter.test(s.slice(term.index+term[0].length))).map(term=>kindOf(term[0])));
  return kinds.size===1?[...kinds][0]:null;
}

export function jointChartType(text=''){
  const s=String(text).normalize('NFKC');
  const matches=[...s.matchAll(terms)];
  if(matches.some(term=>rejectBefore.test(s.slice(0,term.index))||rejectAfter.test(s.slice(term.index+term[0].length))))return null;
  const kinds=new Set();
  for(let i=1;i<matches.length;i++){
    const left=matches[i-1],right=matches[i];
    const leftTable=tableName.test(left[0]),rightTable=tableName.test(right[0]);
    if(leftTable===rightTable)continue;
    const gap=s.slice(left.index+left[0].length,right.index);
    if(!join.test(gap)&&!also.test(gap))continue;
    const lead=s.slice(0,left.index).split(/[。；;！!？?\n]/).at(-1);
    const clause=lead+s.slice(left.index,right.index+right[0].length);
    if(/分别|各自|另一(?:份|个)|另查|另外(?:再|查)|(?:前一半|后一半)/.test(clause))continue;
    if(/^\s*(?:或(?:者|是)?|还是)(?=[^\n。；;]*图)/.test(s.slice(right.index+right[0].length)))continue;
    const chart=leftTable?right[0]:left[0];
    const kind=kindOf(chart);
    if(kind)kinds.add(kind);
  }
  return kinds.size===1?[...kinds][0]:null;
}

export function isExploratory(result){
  return [result?.purpose,result?.result_lineage?.purpose].some(value=>value==='explore'||value==='probe');
}

// Bind a direct caption about a delivered result card to the card the user sees.
// Other chart mentions can be recommendations or refer to a different result.
const resultCaption=new RegExp('^(\\s*(?:本轮|本次|下方)?(?:的)?结果卡\\s*(?:已(?:经)?\\s*)?(?:用|以)\\s*)('+terms.source+')'
  +'(\\s*(?:的)?(?:形式)?\\s*(?:展示|显示|呈现|输出)[。！!]?\\s*)$','u');

export function alignResultChartDescription(answer,receipts){
  const views=new Map(receipts.filter(r=>r.result_ref&&r.visualization?.actual).map(r=>[r.result_ref,r.visualization.actual]));
  if(views.size!==1)return answer;
  const [actual]=views.values();
  let fence=null;
  return answer.split('\n').map(line=>{
    const marker=line.match(/^\s*(`{3,}|~{3,})/)?.[1];
    if(marker){if(!fence)fence=marker;else if(marker[0]===fence[0]&&marker.length>=fence.length)fence=null;return line;}
    if(fence)return line;
    const match=resultCaption.exec(line);
    return match&&kindOf(match[2])!==actual?'结果见下方图卡。':line;
  }).join('\n');
}

export function chartKindSupported(result,kind){
  const types=result?.visualization_options?.available_types;
  return Array.isArray(types)?types.includes(kind):result?.visualization?.actual===kind;
}

const chartActionBefore=new RegExp('(?:用|以|改成|改为|换成|换为|切换到|展示|显示|呈现|绘制|画|生成|给出|提供|附上|要)\\s*(?:一[张个份幅]\\s*)?'+grain+'\\s*$');
const chartActionAfter=/^\s*(?:的形式\s*)?(?:展示|显示|呈现|输出|查看)/;
const chartShown=/已(?:经)?(?:按要求)?\s*(?:生成|绘制|展示|显示|附上)[^，。；\n]{0,16}(?:图|指标卡|仪表盘)|(?:图|指标卡|仪表盘)\s*(?:见下方|如下)/;
const deliveredValue=/(?<![\d.])(?:[−-]?\d[\d,]*(?:\.\d+)?|[零一二两三四五六七八九十百千]+)\s*(?:万?(?:元|次)|亿元|个(?!月)|件|张|本|家|条|项|人|户|份|批|笔|台|吨|平方米|公顷|公里|百分点|[%％])|\|\s*[−-]?\d[\d,]*(?:\.\d+)?\s*(?=\|)/;
const hypothetical=/示例|示意|例如|比如|假设|假如|假定|无法(?:查询|核实|确认|计算|得出)|未能(?:查询|核实|确认|计算|得出)|(?:没有|未查到|缺少)(?:可核实的)?(?:有效)?(?:数据|记录|依据)/;
const countingDefinition=/(?:每(?:一)?[条个笔张份件项]|单[条个笔张份件项])[^。；;，,\n]{0,20}?(?:计为|计作|算作|视为|记为|计|算)\s*[一1]\s*(?:件|张|本|家|条|项|人|户|份|批|笔|台)(?![\p{L}\p{N}])/gu;

export function missingCurrentChartIssues(evidence,userText,answer,{queryExhausted=false}={}){
  const question=String(userText||'').normalize('NFKC');
  const switchKind=chartOnlyKind(question),combined=jointChartType(question);
  const requested=switchKind||combined||explicitChartType(question);
  if(!requested||requested==='table')return [];
  if(!switchKind&&!combined&&![...question.matchAll(terms)].some(term=>kindOf(term[0])===requested&&
    (chartActionBefore.test(question.slice(0,term.index))||chartActionAfter.test(question.slice(term.index+term[0].length)))))return [];
  if(evidence.some(value=>value?.status==='succeeded'&&value.result_ref&&value.visualization&&value.view_url&&
    !isExploratory(value)&&value.validation?.period_grain?.state!=='auxiliary'))return [];
  const clauses=String(answer||'').normalize('NFKC').replaceAll('**','').split(/[。；;！!？?\n]/);
  if(!clauses.some(clause=>chartShown.test(clause)||!hypothetical.test(clause)&&deliveredValue.test(clause.replace(countingDefinition,''))))return [];
  if(queryExhausted)return ['交付缺项：当前未取得可供绘图的正式结果。按已有可核实信息说明本次能确认的范围及具体缺项，勿声称已展示图表。'];
  const action=switchKind?'核对原结果的授权与范围，使用 data_visualize 生成本轮回执。'
    :'按本轮明确的来源、对象和筛选执行正式查询，指定用户要求的图形。';
  return ['交付缺项：本轮尚无正式结果卡。'+action+'查不到数据时如实说明。'];
}

export function chartDeliveryIssues(evidence,userText,answer){
  const kind=jointChartType(userText);
  if(!kind)return [];
  const latest=new Map();
  for(const r of evidence)if(r.result_ref&&r.status==='succeeded'&&r.visualization&&!isExploratory(r)
    &&r.validation?.period_grain?.state!=='auxiliary')latest.set(r.result_ref,r);
  if(latest.size!==1)return [];
  const r=[...latest.values()][0];
  if(r.visualization.actual===kind){
    const table=/^\s*\|?[^\n|]+\|[^\n]+\n\s*\|?\s*:?-{3,}:?\s*\|/m;
    return typeof answer==='string'&&!table.test(answer)?['交付缺项：已显示指定图形，但正文缺少用户同时要求的表格。用本次回执的真实值补充 Markdown 表格，保留现有图形，不重新查询或切换为table。']:[];
  }
  const alternatives=r.visualization_options?.available_types;
  const unavailable=(Array.isArray(alternatives)&&!chartKindSupported(r,kind))||
    (r.visualization.fallback&&(!r.visualization.requested||r.visualization.requested===kind));
  if(unavailable){
    const claimed=/已(?:生成|绘制|展示|显示|附上)(?:了|指定的|所要求的)?[^，。；\n]{0,16}图|(?:柱状图|条形图|折线图|饼图)(?:如下|见下方)/;
    return claimed.test(answer||'')?['当前数据不支持指定图型。正文保留已核算的表格，说明此图暂不能展示，勿声称已绘制。']:[];
  }
  return ['交付缺项：用户同时要求表格和图形，最终结果卡仍未显示指定图形。正文列出表格，结果卡保持'+kind+'；使用data_visualize复用'+r.result_ref+'及最新view_revision='+r.view_revision+'，不要再切回table或重新查询。'];
}

export function explicitChartMismatch(evidence,userText){
  const kind=explicitChartType(userText);
  if(!kind||kind==='table'||jointChartType(userText))return [];
  const charts=evidence.filter(value=>value.result_ref&&value.status==='succeeded'&&value.visualization
    &&!isExploratory(value)&&value.validation?.period_grain?.state!=='auxiliary');
  if(charts.length!==1)return [];
  const [result]=charts;
  if(result.visualization.actual===kind||!chartKindSupported(result,kind))return [];
  return ['交付缺项：当前结果可显示用户指定图形。请用 data_visualize 复用 '+result.result_ref
    +'，kind='+kind+'，expected_revision='+result.view_revision+'。使用实际返回图形描述结果。'];
}
