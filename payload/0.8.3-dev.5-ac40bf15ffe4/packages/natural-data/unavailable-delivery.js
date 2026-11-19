import {requestText} from './request-context.js';
import {extractCatalogSources} from './tool-normalization.js';

const normalize=value=>String(value||'').normalize('NFKC').replaceAll('**','').replace(/\s+/g,' ').trim();
const escape=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const COMPARISON=/环比|同比|基期|上月|上期|上年|同期|可比历史|比较期/;
const MISSING=/缺少|缺失|没有|尚无|无(?:可比|历史|记录)|未提供|不可计算|无法计算|不能计算|未计算|算不了/;
const REQUEST=/请(?:您|你)?(?:先)?(?:确认|选择|提供|补充|补齐|二选一|告知)|(?:需要您|需要你)(?:补充|提供|确认)|是否(?:允许|需要|愿意|可以|改为)|能否(?:提供|补充|确认)|可否(?:提供|补充|确认)/;
const ACTION=/补(?:充|齐)|提供|查询|先查|改为|改用|换用/;

export function requestedUnavailableHistory(events){
  const turn=events.findLast(e=>e.type==='turn/start')?.seq??-1;
  const user=normalize(requestText(events));
  if(!user)return [];
  return extractCatalogSources(events,turn).flatMap(source=>(source.unavailable_metrics||[]).filter(metric=>
    metric.state==='requires_comparable_history'&&[metric.id,metric.name,...(metric.aliases||[])].some(value=>{
      const name=normalize(value);
      if(!name)return false;
      if(!/^[A-Za-z0-9_.-]+$/.test(name))return user.includes(name);
      return new RegExp('(?<![A-Za-z0-9_])'+escape(name)+'(?![A-Za-z0-9_])','i').test(user);
    })));
}

export function asksForMissingHistory(value){
  const text=normalize(value);
  return REQUEST.test(text)&&COMPARISON.test(text)&&ACTION.test(text)
    &&(/(?:可比|同口径|历史|基期)[^。；;]{0,25}(?:数据|数值|数量|记录)|(?:提供|补充|补齐)[^。；;]{0,32}(?:上月|上期|上年|同期|基期|历史)|(?:查询|先查)[^。；;]{0,25}(?:上月|上期|上年|同期|基期)/.test(text));
}

export function hasUnavailableHistoryBoundary(value){
  return normalize(value).split(/[。；;\n]/).some(line=>COMPARISON.test(line)&&MISSING.test(line));
}

export function unavailableDeliveryIssues(text,events){
  const metrics=requestedUnavailableHistory(events);
  if(!metrics.length)return [];
  const plain=normalize(text),issues=[];
  // A comparison value such as 0% must not borrow support from a current
  // scalar result. Bind explicit claims to the configured comparison name.
  const labels=metrics.flatMap(metric=>[metric.name,...(metric.aliases||[])])
    .filter(label=>typeof label==='string'&&/(?:同比|环比)/.test(label))
    .map(label=>normalize(label).replace(/[_\s]+/g,''));
  const fabricated=plain.split(/[。；;\n，,]/).some(clause=>{
    const matches=clause.matchAll(/(?:环比|同比)(?:变化率|增长率|增幅)?\s*(?:为|是|等于|增加|增长|下降|减少|上升|[:：])?\s*(?:-?\d+(?:\.\d+)?\s*%|零|0(?![\d.]))/g);
    return [...matches].some(match=>{
      const prefix=clause.slice(0,match.index);
      if(/(?:不应|不能|不可|不是|并非|不等于|不可以|无法)[^，,]{0,8}$/.test(prefix))return false;
      return /^[\s*#>\-（(]*$/.test(prefix)||labels.some(label=>clause.replace(/[_\s]+/g,'').includes(label));
    });
  });
  if(fabricated)issues.push('本轮所问比较指标缺少可比历史数据，当前不可计算。删除该指标的数值，尤其不要把缺失填成零。保留已核对的其他指标结果。');
  if(!hasUnavailableHistoryBoundary(plain)||!/(?:缺少|缺失|没有|尚无|无|未提供|未覆盖)[^。；;]{0,22}(?:可比|历史|上月|上期|上年|同期|基期)|(?:可比|历史|上月|上期|上年|同期|基期)[^。；;]{0,22}(?:缺失|不足|缺少|不存在|未提供|未覆盖)/.test(plain))
    issues.push('交付缺项：说明所问同比或环比因缺少可比历史数据而不可计算，保留其他已核对的结果。');
  if(asksForMissingHistory(plain))issues.push('交付补充：可比历史数据缺失已确定。直接保留可查询结果和不可计算原因；补齐同口径历史数据后再核算即可，无需让用户在本轮确认、二选一或改查。');
  return issues;
}

export function polishUnavailableDelivery(text,events,{removeTechnical=false}={}){
  const metrics=requestedUnavailableHistory(events);
  if(!metrics.length||!hasUnavailableHistoryBoundary(text))return text;
  let result=String(text);
  // Only remove a standalone final next-step paragraph. Never delete a
  // paragraph that might also carry the actual result or its limitations.
  const paragraphs=result.trimEnd().split(/\n[ \t]*\n/);
  const tail=paragraphs.at(-1)||'';
  if(paragraphs.length>1&&/^\s*(?:如需|如果(?:您|你)?(?:需要|想)|若(?:您|你)?(?:需要|想))/.test(tail)
     &&asksForMissingHistory(tail)&&hasUnavailableHistoryBoundary(paragraphs.slice(0,-1).join('\n'))){
    paragraphs.pop();
    result=paragraphs.join('\n\n');
  }
  if(removeTechnical){
    // Business codes are already present in the user's request or catalog.
    // Keep data and prose intact; the caller revalidates the entire answer.
    const ids=[...(requestText(events).match(/\b[A-Z][A-Z0-9]+_\d+(?:_\d+)+\b/g)||[]),...metrics.map(m=>m.id)]
      .filter(id=>typeof id==='string'&&/^[A-Za-z][A-Za-z0-9_.-]+$/.test(id));
    for(const id of new Set(ids)){
      const code=escape(id);
      result=result.replace(new RegExp('(?<![A-Za-z0-9_])'+code+'[ \\t]+','gi'),'')
        .replace(new RegExp('[（(]\\s*'+code+'\\s*[）)]','gi'),'');
    }
    result=result.replace(/[（(]\s*状态\s*requires_comparable_history\s*[）)]/gi,'');
  }
  return result;
}
