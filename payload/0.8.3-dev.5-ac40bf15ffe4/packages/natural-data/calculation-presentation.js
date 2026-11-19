// Registration/review belongs to configuration management. A computed answer
// explains its actual scope and method, while preserving specific data gaps,
// user quotes/questions, blockquotes, code fences, and Markdown table rows.
const REGISTRATION = new RegExp(
  '(?:(?:当前|本次|本轮|本|该|此|所用|采用的)?(?:计算|统计|指标)?(?:口径|定义|计算规则))'
  + '(?:目前|当前|暂|仍|尚|仅|为|属于|属|是|处于|系|本身|由|按|系统|自动|临时|\\s){0,16}'
  + '(?:推定(?:口径|定义)?|暂定(?:口径|定义)?|(?:尚未|未经|尚待|待|仍待|有待)(?:经)?(?:业务(?:方)?|相关(?:业务)?人员|人工)?(?:进一步)?(?:正式)?(?:确认|核对|核实|审定))'
  + '|(?:属(?:于)?|为|系|按|采用|使用|基于)(?:系统|自动|临时|当前)?(?:推定|暂定)(?:的)?(?:计算|统计)?(?:口径|定义|计算规则)'
  + '|(?:(?:当前|本次|本轮|本|该|此)?(?:SQL|查询|汇总|统计|计算|结果|数值)[^。；;，,\\n]{0,16})?'
  + '(?:尚未|仍未|暂未|没有|未)(?:关联|绑定|对应|匹配)(?:到)?(?:任何)?(?:已确认的|正式|业务|已发布|已登记|配置)?(?:业务)?(?:指标|口径|定义)'
  + '|(?:尚未|仍未|暂未|没有|未)(?:配置|登记|注册|纳入|备案)(?:为|成|入)?(?:正式|已发布|发布|业务){0,2}(?:指标|口径|指标体系|指标库)'
  + '|(?:没有|尚无|未找到)(?:匹配的|对应的|相关的)?(?:已发布的?|已登记的?|预先配置的?|正式的)(?:业务)?(?:指标|口径|指标定义)'
  + '|(?:尚未|未经|尚待|待|仍待|有待)(?:经)?(?:业务(?:方)?|相关(?:业务)?人员|人工)(?:进一步)?(?:正式)?确认'
  + '|(?:请(?:[^。；;，,\\n]{0,16}?)|需|须|尚待|仍待|有待|待)(?:业务(?:方)?|相关(?:业务)?人员|人工)?(?:进一步)?(?:核对|核实|确认|审定)(?:无误)?(?:后|之后)?(?:再)?(?:正式)?(?:采用|报送|作为正式(?:报送|统计)?(?:值|结果|口径)?(?:采用|使用|报送)?)'
  + '|(?:(?:该|此|本次|当前|上述)(?:数值|结果|数据)|(?:数值|结果))(?:仍|尚|暂)?(?:尚未|未经|尚待|待)(?:业务(?:方)?|人工)?(?:正式)?确认(?:(?:后|之后)?(?:再)?(?:正式)?(?:采用|报送|作为正式(?:报送|统计)?(?:值|结果|口径)?(?:采用|使用|报送)?))?'
);

const QUOTED = /“[^”\n]*”|「[^」\n]*」|『[^』\n]*』|‘[^’\n]*’|"[^"\n]*"|'[^'\n]*'|`[^`\n]*`/g;
const META_OR_NEGATION = /(?:为什么|为何|怎么会|为啥|干嘛|原话|以前(?:曾)?显示|之前(?:曾)?显示|上次(?:报告)?(?:说|显示|提示)|此前(?:曾)?(?:显示|提示)|(?:不再|不会|并未|并没有|未再|不)(?:显示|提示|输出|展示|要求|需要)|(?:无需|不需要|不必|免去|取消|去掉|去除|删除|剥离|并非|不是)(?:业务(?:方)?|人工)?(?:确认|核对|提示|推定))/;
const DANGLING_LEAD_IN = /^(?:[-*>#]+\s*)?(?:(?:口径)?说明[：:]\s*)?(?:以上为|本次|本轮|当前)?[^，,。；;]{0,24}(?:基础查询|查询|统计|计算)(?:的)?(?:统计|计算)?(?:口径|定义)(?:[（(][^）)]*[）)])?\s*[，,]?\s*$/;
const COMPANION_CAVEAT = /^(?:[-*>#]+\s*)?(?:(?:上述|以上)?(?:结果|数据|数值))?仅供参考[，,。；;！!]*$/;
const FENCE_OPEN = /^\s*(`{3,}|~{3,})/;
const SENTENCES = /[^。；;！!？?]+[。；;！!？?]*|[。；;！!？?]+/g;
const DATA_GAP = /权限不足|无(?:查询|访问|读取)权限|未(?:获|获得)(?:查询|访问|读取)?权限|未授权|分母(?:为|是)\s*(?:0(?!\d)|零)|无(?:对应|相关|所需)(?:业务记录|业务字段|数据来源)|(?:缺少|缺失|没有|尚无|未提供|未保存|未覆盖)[^。；;，,\n]{0,26}(?:历史(?:记录|数据|时点|快照)?|观测|数据记录|字段|分子|分母|关联(?:关系|键)|(?:日|月|年)(?:的)?(?:数据|记录))|(?:历史(?:数据|记录|快照)|所需字段|分母)[^。；;，,\n]{0,15}(?:缺失|未保存|未覆盖|缺少|不足)/;

const DEFINITION_QUALIFIERS = '(?:(?:可匹配|可对应|匹配|对应|相关|结构化|预置|业务)(?:的)?){0,3}';
const PUBLISHED_DEFINITION = '(?:已(?:经)?(?:发布|登记)(?:的)?)';
const DEFINITION_ITEM = DEFINITION_QUALIFIERS+PUBLISHED_DEFINITION+DEFINITION_QUALIFIERS+'(?:指标|口径)(?:定义)?';
const DATA_INVENTORY = '(?:业务表|数据表|授权表|统计表|明细表|表字段|表名(?:称)?|字段(?:名(?:称)?)?|记录|日志)';
const DATA_QUALIFIERS = '(?:(?:当前|全部|所有|现有|相关|对应|必要|所需|可用|可查|授权|统计|实际|有效|历史|原始|业务|数据)(?:的)?){0,3}';
const DATA_ITEM = DATA_QUALIFIERS+DATA_INVENTORY;
const CONFIG_ITEM = '(?:'+DEFINITION_ITEM+'|'+DEFINITION_QUALIFIERS
  +'(?:(?:指标|口径)(?:定义|配置|发布状态|登记状态)|(?:已)?(?:发布|登记|预置)(?:的)?(?:指标|口径|定义)'
  +'|(?:查询|配置|SQL[ \\t]*)?(?:示例|样例|模板))|指标|(?:业务|统计)?口径)(?:名(?:称)?)?';
const LIST_ITEM = '(?:'+DATA_ITEM+'|'+CONFIG_ITEM+')';
const LIST_MEMBER = '(?:\\*\\*'+LIST_ITEM+'\\*\\*|'+LIST_ITEM+')';
const LIST_SEPARATOR = '[ \\t]*(?:以及|或者|和|与|及|或|、|[，,][ \\t]*(?:以及|或者|和|与|及|或)?)[ \\t]*';
const DEFINITION_ATTRIBUTE = '(?:名称|口径|定义)';
const DEFINITION_MATCH = DEFINITION_ATTRIBUTE+'(?:(?:或|与|和|及)'+DEFINITION_ATTRIBUTE+')?'
  +'[ \\t]*(?:与|和|同|跟)[^，,。；;！!？?\\n]{1,64}?相?(?:匹配|对应|一致)的[ \\t]*';
const DEFINITION_MISSING = '(?:(?:也|另外|同时|并|尚|仍|暂(?:时)?|虽然|尽管)[ \\t]*){0,3}'
  +'(?:没有|无|(?:没|未)(?:能)?(?:查询到|查找到|检索到|匹配到|找到|查到)|未命中)'
  +'(?:'+DEFINITION_MATCH+')?'+DEFINITION_ITEM;
const PUBLICATION_ONLY = new RegExp('^(?:[-*#]+\\s*)?(?:当前|本次|本轮)?(?:(?:授权)?(?:目录|指标库|业务口径)(?:中|内)\\s*)?'
  +'(?:'+DEFINITION_MISSING+'|(?:目录和)?已发布指标中没有(?:任何)?带[^。；;\\n]{1,60}字样的(?:业务)?指标(?:或口径)?)[，,。；;！!]*$');
const EMPTY_REGISTRY = /^(?:[-*#]+\s*)?(?:(?:当前|本|该)(?:项目|来源|数据源|系统|库)(?:中|里|内)?\s*)?(?:没有|尚未|暂未|未)(?:发布|登记|配置)(?:任何)?(?:相关|对应|预置|业务|结构化)*(?:指标|口径)(?:定义)?[，,。；;！!]*$/;
const DATA_LIST_MEMBER = new RegExp('^'+DATA_ITEM+'$');
const INVENTORY_QUALIFIER = '(?<qualifier>(?:(?:与|和|同|跟|针对)[^，,。；;！!？?、\\n]{1,64}?)?(?:相关|对应|匹配|有关)的)?';
const INVENTORY_LIST = new RegExp(INVENTORY_QUALIFIER+LIST_MEMBER+'(?:'+LIST_SEPARATOR+LIST_MEMBER+'){1,12}'
  +'(?=[ \\t]*(?:[，,。；;！!：:]|[（(][^）)\\n]{0,120}(?:字段|列名|数据项)|(?:与|和)(?:其|之|该对象|所问对象)(?:相?对应|匹配|相关)|$))','g');
const INVENTORY_SEPARATOR = new RegExp('('+LIST_SEPARATOR+')');
const APPENDED_PUBLICATION = new RegExp('(?:[，,]\\s*)?[ \\t]*(?:且|并且|而且|同时)\\s*(?:\\*\\*)?'+DEFINITION_MISSING
  +'(?:\\*\\*)?(?=[ \\t]*[，,。；;！!]|[ \\t]*$)','g');
const REGISTRY_LOOKUP_STATUS = new RegExp(
  '(?:在)?(?:当前|目前|现有|本项目)?(?:的)?(?:已)?(?:授权)?(?:目录|指标库|配置清单|业务配置)(?:中|内|里)?\\s*'
  +'(?:也|还|仍)?(?:尚未|暂未|没有|未)(?:发布|登记|录入|定义|配置)\\s*'
  +'(?:\\*\\*)?(?:_{2,}|[^_，,。；;！？?：:\\n]{1,44}?)(?:\\*\\*)?\\s*'
  +'(?:的)?(?:相关|对应|匹配|正式|已发布|业务){0,3}(?:的)?(?:业务)?(?:口径|指标)(?:定义)?\\s*'
  +'(?:[，,]\\s*(?:也|还|同时|但(?:是)?|不过|并且?)?\\s*|[。；;！!]|$)','g');
const INVENTORY_GAP = /缺少|缺失|没有|未(?:见|查到|找到|包含|涉及|覆盖|记录)|不(?:存在|包含|涉及|覆盖|记录)|无(?:匹配|相关|对应)/;
const CONFIG_LIST_GAP = /^\s*(?:[-*#]+\s*)?(?:(?:当前|本项目|该项目)(?:的)?\s*)?(?:已(?:发布|登记)(?:的)?(?:指标|口径)|(?:指标|口径)(?:发布|登记|配置))(?:列表|目录|清单|库)?(?:中|里|内)?\s*(?:也|仍|同样|尚)?\s*(?:没有|没有找到|未找到|未列出|未包含|缺少|无)[^。；;！!？?\n]*[。；;！!]*\s*$/;
const INTERNAL_ITEM_CODE = /\b[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)*[_-]\d+(?:[_-]\d+)*\b/i;
const CATALOG_DIAGNOSTIC_SUBJECT = /(?:查询|统计|报表)脚本|指标(?:库|目录|清单|编目|登记|配置|编号|编码|定义)|口径(?:编目|登记|编号|编码|配置)|(?:指标|口径)[^，,。；;]{0,8}(?:编目|脚本)|目录(?:中|内|里|明确)?(?:说明|记录|注明|标注|记载|指出)/;
const CATALOG_PROCESS_STATE = /(?:未|没有|没|待|正在)(?:实现|整理|编目|配置|登记|录入|开发|上线|编写)|(?:尚无|缺少|缺乏|缺)(?:指标|查询|统计|报表)?(?:脚本|配置|编目)/;
const CATALOG_PUBLICATION_GAP = /(?:没有|未找到|无)[^。；;，,\n]{0,60}已(?:发布|登记)(?:的)?(?:业务|结构化)?(?:指标|定义|口径)[，,。；;！!]*$/;
const EXAMPLE_SCOPE = /(?:示例|样例|模板)[^。；;，,\n]{0,28}(?:为例|(?:仅|只)(?:覆盖|包含|提供)|(?:选用|使用|采用)了?\s*\d{4}\s*年)/;
const BUSINESS_DETAIL = /(?:数据(?:来源|源)|来源)\s*(?:为|是|来自|[：:])|数据来自|(?:缺少|未见|未采集)[^。；;，,\n]{0,24}(?:数据|记录|字段|明细)|未(?:记录|采集|存储|保存)[^。；;，,\n]{0,24}(?:明细|记录|字段|次数|数据)/;
const DATA_FIELD_OFFER = /(?:提供|补充|给出)[^。；;！？\n]{0,55}(?:表名|字段名|对应数据表|原始记录|业务日志)/;
const MISSING_SOURCE = /(?:没有|不存在|缺少|未找到|找不到)(?:(?:该|此类|对应|相关|所需|可查)(?:的)?){0,2}(?:数据来源|来源|数据源|数据表|业务表|授权表)/;
const SOURCE_OFFER = /(?:提供|补充|给出)[^，,。；;]{0,16}(?:数据来源|数据源)/;
const SOURCE_OR_DEFINITION = /(?<=数据来源|数据源)[ \t]*(?:或者|或)[ \t]*(?:相应|对应|相关)(?:的)?(?:业务|统计)?口径(?=[ \t]*(?:[，,。；;！!]|$))/g;
const ADD_DEFINITION_INSTEAD = /(?:[，,]\s*)?(?:或者|或)\s*在(?:现有|已配置的?)?(?:业务|统计)?口径(?:中|里)(?:补充|登记|配置)(?:该|相应|对应)?(?:业务)?(?:指标|口径)(?:定义)?(?:之后|后)?(?=\s*(?:[，,。；;！!]|$))/g;
const ASKS_CALCULATION = /如何(?:计算|统计)|怎么(?:计算|算|统计)|按(?:何种|什么|哪种|哪个)(?:方式|口径|规则)|计算(?:方法|方式|公式|口径)|统计(?:方法|方式|口径|规则)|(?:口径|定义)(?:是什么|有什么区别|如何选)/;

function maskQuotes(text) {
  return text.replace(QUOTED, ' __Q__ ');
}

const maskQuotedSpans = text => text.replace(QUOTED,quote=>'_'.repeat(quote.length));
const maskCatalogGroups = text => maskQuotedSpans(text)
  .replace(/[（(][^（）()\n]*[）)]/g,group=>group.replace(/[，,。；;！!？?]/g,'_'));
function segments(text,pattern,mask=maskQuotedSpans) {
  return [...mask(text).matchAll(pattern)].map(match=>text.slice(match.index,match.index+match[0].length));
}

function isProtectedSentence(sentence) {
  const plain = sentence.replaceAll('**', '').trim();
  if (!plain) return false;
  if (/[？?][”」』’"'\s]*$/.test(plain)) return true;
  if (META_OR_NEGATION.test(plain)) return true;
  return false;
}

export const hasConcreteDataGap = text => DATA_GAP.test(text);

function keepGapFromRegistration(clause) {
  if (!hasConcreteDataGap(maskQuotes(clause))) return '';
  if (maskQuotes(clause)!==clause) return clause;
  const parts=clause.split(/并且|而且|但是|且|但/);
  const facts=parts.filter(part=>!REGISTRATION.test(part.replaceAll('**','')));
  if (!facts.some(hasConcreteDataGap)) return clause;
  const end=clause.trimEnd().match(/[，,。；;！!？?]+$/)?.[0]||'';
  return facts.join('且').trim().replace(/[，,。；;！!？?]+$/, '')+end;
}

function cleanSentence(sentence) {
  if (isProtectedSentence(sentence)) return sentence;
  const masked = maskQuotes(sentence.replaceAll('**', '').trim());
  if (!REGISTRATION.test(masked)) return sentence;
  const clauses = segments(sentence,/[^，,]+[，,]*|[，,]+/g);
  const kept = [];
  let removed = false;
  for (const clause of clauses) {
    const plain = clause.replaceAll('**', '').trim();
    if (!META_OR_NEGATION.test(plain) && REGISTRATION.test(maskQuotes(plain))) {
      const keptGap=keepGapFromRegistration(clause);
      if(keptGap)kept.push(keptGap);
      if(keptGap!==clause)removed=true;
    } else {
      kept.push(clause);
    }
  }
  if (!removed) return sentence;
  if (!kept.length) return '';
  if (kept.every(c => COMPANION_CAVEAT.test(c.replaceAll('**', '').trim()))) return '';
  const filtered = kept.filter(c => !COMPANION_CAVEAT.test(c.replaceAll('**', '').trim()));
  if (!filtered.length) return '';
  let joined = filtered.join('').trim();
  if (DANGLING_LEAD_IN.test(joined.replaceAll('**', ''))) return '';
  const termMatch = sentence.trim().match(/[。；;！!？?]+$/);
  if (termMatch && /[，,、]+$/.test(joined)) {
    joined = joined.replace(/[，,、]+$/, termMatch[0]);
  }
  return joined;
}

export function* proseLines(text) {
  let fence = null;
  for (const line of String(text || '').split('\n')) {
    const fenceMatch = FENCE_OPEN.exec(line);
    if (!fence && fenceMatch) {
      fence = {char: fenceMatch[1][0], len: fenceMatch[1].length};
      yield {line,protected:true};
      continue;
    }
    if (fence) {
      const closeRe = new RegExp(`^\\s*\\${fence.char}{${fence.len},}\\s*$`);
      if (closeRe.test(line)) fence = null;
      yield {line,protected:true};
      continue;
    }
    yield {line,protected:/^\s*>/.test(line)||/^\s*\|.*\|\s*$/.test(line)};
  }
}

export function removeUnquotedMatches(line,pattern) {
  let out='',end=0;
  for (const match of maskQuotedSpans(line).matchAll(pattern)) {
    out+=line.slice(end,match.index);
    end=match.index+match[0].length;
  }
  return out+line.slice(end);
}

export function asksAboutCatalogRegistration(text) {
  return String(text||'').normalize('NFKC').split(/[。！？!?；;\n]/).some(part=>{
    if(/(?:不需要|不用|不要|无需|不必|别)(?:再|额外|特意)?(?:说明|告知|提供|显示|展示|列出|提及)?[^，,]{0,12}(?:发布|登记|配置|指标库)/.test(part))return false;
    return /(?:发布|登记|配置)(?:的)?(?:状态|情况|记录)|指标库(?:清单|设置)/.test(part)
      || /(?:列出|查看|展示|查询|显示|检查|核对|有哪些|有无|有没有|是否有|多少个?)(?:一下)?[ \t]*(?:(?:当前|本项目|项目|目前|系统|配置中|库中)(?:的)?){0,2}(?:哪些|所有|全部|相关|对应)?(?:的)?已(?:经)?(?:发布|登记)(?:的)?(?:相关|对应|业务|结构化)?(?:的)?(?:指标|口径)/.test(part)
      || /已(?:经)?(?:发布|登记|配置)(?:了)?(?:哪些|什么|多少(?:个)?)(?:业务|结构化)?(?:指标|口径)/.test(part)
      || /(?:指标|口径)(?:是否|是否已经|是否已|有无|有没有)(?:发布|登记|配置)|(?:指标|口径)(?:已经|已)?(?:发布|登记|配置)了吗$/.test(part)
      || /(?:查看|列出|显示|请问|查询|核对)[^。！？；]{0,26}(?:目录编号|指标(?:编号|编码)|脚本(?:状态|实现情况|实现状态)|编目(?:状态|情况))|脚本(?:是否|有没有|有无|是否已经)(?:已)?实现/.test(part);
  });
}

const SYNTHETIC_MEASUREMENT_SUFFIX=/([（(])(?:模拟|合成)([日周月季年](?:度)?(?:发生量|累计量|观测量|观测值|统计值))([）)])$/;

// Read the qualifier from the bound metric name and only project a display label.
// The metric, its identifier, result rows and source provenance remain available.
function publicMetricName(result) {
  const metric=result?.metric_contract, contract=result?.answer_contract;
  if(result?.status!=='succeeded'||!result.result_ref||result.receipt?.read_only!==true
    ||result.source_provenance?.synthetic!==true||contract?.source?.synthetic!==true
    ||contract.result_ref!==result.result_ref||!metric?.id||metric.id!==contract.metric_id
    ||[result.purpose,result.result_lineage?.purpose].some(purpose=>purpose==='explore'||purpose==='probe')
    ||typeof metric.name!=='string')return null;
  const label=metric.name.replace(SYNTHETIC_MEASUREMENT_SUFFIX,'$1$2$3');
  return label===metric.name?null:label;
}

export function publicMetricLabel(text,result) {
  const label=publicMetricName(result);
  return label&&typeof text==='string'?text.replaceAll(result.metric_contract.name,()=>label):text;
}

export function publicMetricTitles(text,receipts,question='') {
  if(asksAboutCatalogRegistration(question)||/(?:指标|口径)(?:的)?(?:原名|正式名称|全称)/.test(question))return text;
  const normalizedQuestion=String(question||'').normalize('NFKC');
  const names=new Map();
  for(const result of receipts){
    const name=result?.metric_contract?.name;
    if(typeof name!=='string'||!name||normalizedQuestion.includes(name.normalize('NFKC')))continue;
    const label=publicMetricName(result);
    names.set(name,(label&&(!names.has(name)||names.get(name)===label))?label:null);
  }
  for(const [name,label] of names)if(label===null)names.delete(name);
  if(!names.size)return text;
  // The model may shorten the official name while retaining its source
  // qualifier. Project that exact qualifier when one result owns the answer.
  if(receipts.length===1){
    const name=receipts[0].metric_contract?.name;
    const label=names.get(name);
    const qualifier=label&&name.match(SYNTHETIC_MEASUREMENT_SUFFIX)?.[0];
    if(qualifier&&!normalizedQuestion.includes(qualifier.normalize('NFKC'))){
      names.set(qualifier,qualifier.replace(SYNTHETIC_MEASUREMENT_SUFFIX,'$1$2$3'));
    }
  }
  const project=part=>{
    for(const [name,label] of names)part=part.replaceAll(name,()=>label);
    return part;
  };
  return [...proseLines(text)].map(({line,protected:literal})=>{
    if(literal)return line;
    let offset=0,display='';
    for(const quote of line.matchAll(QUOTED)){
      const written=quote[0], metric=written.slice(1,-1);
      const adopted=written[0]!=='`'&&SYNTHETIC_MEASUREMENT_SUFFIX.test(metric)&&names.get(metric)
        &&/(?:按|按照|采用|使用|基于|统计对象(?:为|是))\s*$/.test(line.slice(0,quote.index));
      display+=project(line.slice(offset,quote.index))+(adopted?written[0]+names.get(metric)+written.at(-1):written);
      offset=quote.index+quote[0].length;
    }
    return display+project(line.slice(offset));
  }).join('\n');
}

function isCatalogDiagnostic(clause,catalogContext) {
  const prose=maskQuotes(clause.replaceAll('**',''));
  if(hasConcreteDataGap(prose)||BUSINESS_DETAIL.test(prose))return false;
  const catalogSubject=CATALOG_DIAGNOSTIC_SUBJECT.test(prose);
  const implementationArtifact=catalogSubject||catalogContext&&/脚本|编目/.test(prose);
  return EXAMPLE_SCOPE.test(prose)||CATALOG_PUBLICATION_GAP.test(prose.trim())
    || implementationArtifact&&CATALOG_PROCESS_STATE.test(prose)
    || INTERNAL_ITEM_CODE.test(prose)&&(catalogSubject||/(?:指标|口径)(?:所属|归入|属于|对应|编号|归为|划入)/.test(prose));
}

function physicalInventory(sentence,requiredData) {
  const unquoted=maskQuotedSpans(sentence);
  let visible='', previous=0;
  for(const match of unquoted.matchAll(INVENTORY_LIST)){
    const start=match.index+(match.groups.qualifier?.length||0),end=match.index+match[0].length;
    const original=sentence.slice(start,end);
    const before=unquoted.slice(0,start).split(/[，,]/).at(-1);
    const after=unquoted.slice(end).replace(/^[ \t]*(?:[（(][^）)]*[）)])?[ \t]*[，,]?/,'').split(/[，,]/,1)[0];
    // Keep the list attached to the actual inventory gap and preserve any shared qualifier.
    const gap=requiredData||INVENTORY_GAP.test(before)||INVENTORY_GAP.test(after);
    const parts=original.split(INVENTORY_SEPARATOR);
    const items=parts.filter((_,index)=>index%2===0);
    const data=items.flatMap((item,index)=>DATA_LIST_MEMBER.test(item.replaceAll('**',''))?[index]:[]);
    const text=gap&&data.length&&data.length<items.length
      ?data.map((index,position)=>(position?parts[2*index-1]:'')+items[index]).join('')
      :original;
    visible+=sentence.slice(previous,start)+text;
    previous=end;
  }
  return visible+sentence.slice(previous);
}

export function catalogRegistrationText(line,question='',confirmedDataGap=false) {
  const missingSource = !ASKS_CALCULATION.test(question)&&MISSING_SOURCE.test(maskQuotes(line));
  return segments(line,SENTENCES,maskCatalogGroups).map(sentence=>{
    if(isProtectedSentence(sentence))return sentence;
    const unquoted=maskQuotes(sentence.replaceAll('**',''));
    if(confirmedDataGap&&CONFIG_LIST_GAP.test(unquoted)&&!hasConcreteDataGap(unquoted))return '';
    const catalogContext=CATALOG_DIAGNOSTIC_SUBJECT.test(unquoted);
    const dataOffer=confirmedDataGap&&DATA_FIELD_OFFER.test(unquoted);
    let visible=INVENTORY_GAP.test(unquoted)||dataOffer?physicalInventory(sentence,dataOffer):sentence;
    if(missingSource&&SOURCE_OFFER.test(maskQuotes(sentence)))
      visible=removeUnquotedMatches(visible,SOURCE_OR_DEFINITION);
    if(confirmedDataGap&&!ASKS_CALCULATION.test(question))
      visible=removeUnquotedMatches(visible,ADD_DEFINITION_INSTEAD);
    visible=removeUnquotedMatches(visible,APPENDED_PUBLICATION);
    visible=removeUnquotedMatches(visible,REGISTRY_LOOKUP_STATUS);
    const clauses=segments(visible,/[^，,]+[，,]*|[，,]+/g,maskCatalogGroups);
    const kept=clauses.filter(clause=>{
      const plain=maskQuotes(clause.replaceAll('**','')).trim();
      return !PUBLICATION_ONLY.test(plain)&&!EMPTY_REGISTRY.test(plain)
        && !(confirmedDataGap&&isCatalogDiagnostic(clause,catalogContext));
    });
    if(kept.length===clauses.length)return visible;
    const ending=sentence.trimEnd().match(/[。；;！!]+$/)?.[0]||'';
    const prose=kept.join('').trimEnd().replace(/[，,、]+$/,ending);
    return kept[0]===clauses[0]?prose:prose.replace(/^(\s*)(?:并且|而且|但是|不过|且|但)\s*/,'$1');
  }).join('');
}

export function publicAnswerProse(text) {
  return [...proseLines(text)].filter(item=>!item.protected)
    .flatMap(({line})=>maskQuotes(line.replaceAll('**','')).match(SENTENCES)||[])
    .filter(sentence=>!isProtectedSentence(sentence))
    .join('\n');
}

export function calculationText(text) {
  const outLines = [];
  for (const {line,protected:literal} of proseLines(text)) {
    if (literal) {
      outLines.push(line);
      continue;
    }
    const sentences = segments(line,SENTENCES);
    if (!sentences.length) {
      outLines.push(line);
      continue;
    }
    const cleaned = sentences.map(cleanSentence);
    if (cleaned.every((s, idx) => s === sentences[idx])) {
      outLines.push(line);
      continue;
    }
    const joined = cleaned.join('').trim().replace(/^(?:[-*>#]+\s*)?(?:口径说明|说明)[：:]\s*$/, '');
    outLines.push(joined);
  }
  return outLines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

export const calculationNotes = notes => (Array.isArray(notes) ? notes : [])
  .filter(note => typeof note === 'string').map(calculationText).filter(Boolean);
