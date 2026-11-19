// Arithmetic across separate metric receipts needs matching provenance and series.
export function periodEvidence(evidence, userText=''){
  const series=new Map(), percentages=new Set(), totals=new Map(), numbers=new Set();
  const thresholds=[...userText.matchAll(/(\d+(?:\.\d+)?)\s*[%％]/g)].map(m=>Number(m[1]));
  for(const e of evidence){
    const c=e.metric_contract,source=e.receipt?.source_id||e.source_id||c?.semantic_spec?.source_id;
    if(!c||!e.result_ref||e.truncated||e.facts?.length!==e.row_count)continue;
    for(const fact of e.facts||[]){
      if(!Number.isFinite(fact.value))continue;
      if(c.unit==='%')for(const t of thresholds)percentages.add(Math.abs(fact.value-t));
      const {period,...dims}=fact.dimensions||{};
      if(!source||!c.id||!/^\d{4}(?:-\d{2})?$/.test(period))continue;
      const key=JSON.stringify([source,c.id,c.version,c.unit,c.formula,c.default_filters,c.scope?.filters||[],c.scope?.dimensions||[],Object.entries(dims).sort(),period.length]);
      if(!series.has(key))series.set(key,{points:new Map(),contract:c});
      const points=series.get(key).points;
      points.set(period,points.has(period)&&points.get(period)!==fact.value?null:fact.value);
    }
  }
  for(const {points,contract} of series.values()){
    if([...points.values()].includes(null))continue;
    const values=[...points.values()].slice(-60);
    for(const a of values)for(const b of values){
      numbers.add(Math.abs(a-b));
      if(b)numbers.add(Math.abs((a-b)/b*100));
    }
    if(!contract.additive_over_time||contract.ratio)continue;
    for(const year of new Set([...points.keys()].map(p=>p.slice(0,4)))){
      for(const [first,last] of [[1,6],[7,12],[1,12]]){
        const months=Array.from({length:last-first+1},(_,i)=>points.get(year+'-'+String(i+first).padStart(2,'0')));
        if(!months.every(Number.isFinite))continue;
        const total=months.reduce((a,b)=>a+b,0);
        numbers.add(total);
        if(first===1&&last===12){
          if(!totals.has(contract.unit))totals.set(contract.unit,new Set());
          totals.get(contract.unit).add(total);
        }
      }
    }
  }
  return {numbers,percentages,totals};
}
