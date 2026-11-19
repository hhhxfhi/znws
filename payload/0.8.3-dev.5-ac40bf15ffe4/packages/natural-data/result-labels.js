const UNIT=/^[（(](?:本|张|件|家次|家|户|项次|项|条|人|个|台|套|笔|部|组|批|宗|份|辆|(?:人民币)?(?:万|亿)?元|万次|次|%|％|个百分点|千?米|平方(?:千)?米|公顷|亩|吨|千克|克)[）)]/;
export function conciseLabel(label, unit){
 const value=String(label||''),at=value.search(/[（(，,；;。]/);
 if(at<0)return value;
 const suffix=unit?'（'+unit+'）':(UNIT.exec(value.slice(at))?.[0]||'');
 return value.slice(0,at)+suffix;
}
// Presentation only: retain year and month, never rewrite stored facts or keys.
export function chinesePeriodLabel(label){
 const value=String(label??'');
 const match=/^(20\d{2})-(0[1-9]|1[0-2])$/.exec(value);
 return match?`${match[1]}年${Number(match[2])}月`:value;
}
export function missingChartUnit(result){
 const {type,encoding={}}=result.chart||{};
 const fields=[encoding.y,encoding.value,encoding.size];
 if(['histogram','scatter','bubble'].includes(type))fields.push(encoding.x);
 return fields.flat().some(f=>f&&typeof f==='object'&&!f.unit&&!result.column_unit_evidence?.[f.field]?.unit
    &&!UNIT.test(String(f.label||'').slice(String(f.label||'').search(/[（(]/))))&&!result.metric_contract?.unit;
}
