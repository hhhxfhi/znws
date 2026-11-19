// Present immutable server claims; never recalculate a rate in the browser.
export function comparisonPresentation(result){
 if(result.inspection_reconciliation?.rows?.length){
  const checks=result.inspection_reconciliation.rows;
  return {title:'检查明细复核',columns:['期间','抽查单位数','三项全部合格单位数','明细复核率','状态'],column_labels:{抽查单位数:'抽查单位数（家）',三项全部合格单位数:'三项全部合格单位数（家）',明细复核率:'明细复核率（%）'},
   rows:checks.map(r=>({期间:r.dimensions.period,抽查单位数:r.denominator,三项全部合格单位数:r.numerator,明细复核率:r.detail_rate,状态:r.status==='incomplete'?'检查结论不完整':r.status==='conflict'?'正式指标待复核':'本次校验一致'})),row_count:checks.length,truncated:false,status:'succeeded',chart:{type:'table',encoding:{}},notes:checks.map(r=>r.note)};
 }
 const latest=new Map();
 for(const c of result.verified_claims||[]){
  if(!c.claim_id||!['period_compare','aggregate_compare','compare_values'].includes(c.operation)||!c.baseline||!c.current)continue;
  const key=JSON.stringify([c.metric_id,c.baseline.id,c.current.id]);latest.set(key,c);
 }
 const claims=[...latest.values()];
 if(!claims.length)return null;
 const unit=claims[0].current.unit,diffUnit=claims[0].difference_unit||unit;
 if(!unit)return null;
 if(claims.some(c=>c.current.unit!==unit||c.baseline.unit!==unit||(c.difference_unit||unit)!==diffUnit))return null;
 const group=c=>Object.entries(c.current.dimensions||{}).filter(([k])=>!['period','aggregation'].includes(k)).map(([k,v])=>`${result.column_labels?.[k]||k}：${v}`).join('，');
 const hasGroup=claims.some(c=>group(c)),hasThreshold=claims.some(c=>c.change_threshold!==undefined);
 const columns=[...(hasGroup?['分组']:[]),'基期','本期','基期值','本期值','增减量','变化率',...(hasThreshold?['阈值判断']:[])];
 const columnsWithUnits={基期值:`基期值（${unit}）`,本期值:`本期值（${unit}）`,增减量:`增减量（${diffUnit}）`,变化率:'变化率（%）'};
 const rows=claims.map(c=>({...(hasGroup?{分组:group(c)}:{}),
  基期:(c.baseline.dimensions?.period||'基期')+(c.operation==='aggregate_compare'?'合计':''),
  本期:(c.current.dimensions?.period||'本期')+(c.operation==='aggregate_compare'?'合计':''),
  基期值:c.baseline.value,本期值:c.current.value,增减量:c.difference,变化率:c.change_percent,
  ...(hasThreshold?{阈值判断:c.change_threshold===undefined?'—':c.change_matched===true?'已触发':c.change_matched===false?'未触发':'无法判断'}:{})}));
 return {columns,column_labels:columnsWithUnits,rows,row_count:rows.length,truncated:false,
  title:'期间对照表',status:'succeeded',chart:{type:'table',encoding:{}},
  notes:[...new Set(claims.filter(c=>c.operation==='aggregate_compare'||c.zero_base||c.negative_base).map(c=>c.verified_text).filter(Boolean))]};
}
