// Recompute a stated share from a complete, unambiguous grouped result.
// This verifies arithmetic only, not completeness of the business population.
export function derivedShareIssues(text,evidence){
  const issues=[],seen=new Set(),series=[];
  for(const e of evidence){
    if(!e.result_ref||seen.has(e.result_ref)||e.purpose==='explore'||e.status!=='succeeded')continue;
    seen.add(e.result_ref);
    const v=e.values;
    if(!v?.complete||v.rows.length<2||v.columns.length!==2||e.metric_contract?.ratio)continue;
    const dims=[0,1].filter(i=>v.rows.every(r=>typeof r[i]==='string'&&r[i].trim()&&!/^\d/.test(r[i])));
    if(dims.length!==1)continue;
    const d=dims[0],m=1-d;
    if(/率|比例|占比|百分比|%|rate|ratio|percent/i.test(v.columns[m])||!v.rows.every(r=>typeof r[m]==='number'&&Number.isFinite(r[m])&&r[m]>=0))continue;
    if(new Set(v.rows.map(r=>r[d])).size!==v.rows.length)continue;
    const total=v.rows.reduce((sum,r)=>sum+r[m],0);
    if(total>0)series.push({rows:v.rows,d,m,total});
  }
  for(const clause of String(text).replaceAll('**','').split(/[。；;\n]/)){
    if(/不能|无法|尚未|不是|并非|不代表|待确认|待核实/.test(clause))continue;
    const claim=clause.match(/(?:合计|共|总计)?\s*(约占|占|占比(?:为)?|占到)[^，,。；;\d一二三四五六七八九十零〇]{0,16}([一二三四五六七八九十]|\d+(?:\.\d+)?)\s*(成|%|％|个百分点)/);
    if(!claim||claim[3]==='个百分点')continue;
    const chinese='零一二三四五六七八九十',n=/^\d/.test(claim[2])?Number(claim[2]):chinese.indexOf(claim[2]);
    const stated=claim[3]==='成'?n*10:n;
    const prefix=clause.slice(0,claim.index),computed=[];
    for(const s of series){
      const alias=label=>label.replace(/(?:省|市|区|县)$/,'');
      const matched=s.rows.filter(r=>prefix.includes(r[s.d])||
        (alias(r[s.d]).length>=2&&s.rows.filter(x=>alias(x[s.d])===alias(r[s.d])).length===1&&prefix.includes(alias(r[s.d]))));
      if(!matched.length||matched.length===s.rows.length)continue;
      computed.push(100*matched.reduce((sum,r)=>sum+r[s.m],0)/s.total);
    }
    // Multiple periods or populations cannot be disambiguated from a bare
    // sentence. Leave that case to the typed comparison contract.
    if(!computed.length||computed.some(v=>Math.abs(v-computed[0])>.01))continue;
    const tolerance=claim[3]==='成'?5:/约/.test(claim[1])?1:.06;
    if(Math.abs(stated-computed[0])>tolerance)
      issues.push('占比与同一完整分组结果不符：'+claim[0]+'，按所列对象之和除以该结果总量为'+computed[0].toFixed(2)+'%。请修正这一算术结论，不扩大统计范围。');
  }
  return issues;
}
