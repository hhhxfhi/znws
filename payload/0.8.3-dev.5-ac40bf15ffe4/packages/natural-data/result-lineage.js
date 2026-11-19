// Only engine-verified, same-request correction receipts retire an answer.
// Merge partial view receipts first; a later chart receipt is not a new query.
export function mergeResultReceipt(prior,value){
  if(!prior)return value;
  const merged={...prior,...value};
  for(const field of ['claims','verified_claims']){
    const claims=[...(prior[field]||[]),...(value[field]||[])];
    if(claims.length)merged[field]=[...new Map(claims.map(claim=>[claim.claim_id||JSON.stringify(claim),claim])).values()];
  }
  return merged;
}

export function mergedEvidence(evidence){
  const byRef=new Map(),other=[];
  for(const value of evidence||[]){
    if(!value?.result_ref){other.push(value);continue;}
    byRef.set(value.result_ref,mergeResultReceipt(byRef.get(value.result_ref),value));
  }
  return [...other,...byRef.values()];
}
export function replacedResultRefs(evidence){
  const merged=mergedEvidence(evidence),byRef=new Map(merged.map(e=>[e?.result_ref,e]));
  return new Set(merged.flatMap(e=>{
    const l=e?.result_lineage,old=byRef.get(l?.replaces_result_ref),p=old?.result_lineage;
    if(e?.status!=='succeeded'||l?.relationship!=='requested_correction'||!l.request_ref||!l.population_fingerprint
      ||['same_request','same_source_configuration','same_population'].some(k=>l.checks?.[k]!=='checked'))return [];
    if(!old||old===e||old.status!=='succeeded'||l.purpose!=='answer'||p?.purpose!=='answer'
      ||JSON.stringify(l.request_ref)!==JSON.stringify(p.request_ref)
      ||['session_id','source_id','config_fingerprint','population_fingerprint'].some(k=>!l[k]||l[k]!==p[k]))return [];
    return [old.result_ref];
  }));
}
export function activeEvidence(evidence){
  const merged=mergedEvidence(evidence),replaced=replacedResultRefs(merged);
  return merged.filter(e=>!replaced.has(e?.result_ref));
}
