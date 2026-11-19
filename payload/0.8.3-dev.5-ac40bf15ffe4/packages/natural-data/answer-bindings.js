import {derivedShareIssues} from './derived-share.js';
import {isContractConfirmed} from './metric-semantics.js';
export {isContractConfirmed} from './metric-semantics.js';

// Associate a count claim with its own metric result, not the pool of numbers.
const COUNT_UNITS = new Set(['个', '家', '人', '户', '本', '张', '份', '件', '条', '笔', '台', '辆']);
const subjects = text => [...String(text || '').matchAll(/([一-鿿]{2,}?)(?:的)?(?:数量|总量|总数)/g)].map(m => m[1].slice(-2));

// Numbers are excluded only when their text actually denotes a date.
export function citedMetricValues(text) {
  return [...String(text).matchAll(/(-?\d[\d,]*(?:\.\d+)?)/g)]
    .filter(m => !/^年|^[-/]\d{1,2}(?:[-/月]|\b)/.test(text.slice(m.index + m[0].length)))
    .map(m => Number(m[1].replaceAll(',', '')));
}
export function claimReceipts(text, receipts) {
  const named = receipts.filter(r => r.metric_contract?.name && text.includes(r.metric_contract.name));
  if (named.length) return named;
  const nums = citedMetricValues(text);
  const matched = receipts.filter(r => (r.facts || []).some(f => typeof f.value === 'number' && nums.some(n => Math.abs(f.value-n)<1e-4)));
  return matched.length ? matched : receipts;
}

export function bindingIssues(text, evidence) {
  const issues = [...derivedShareIssues(text, evidence)];
  const plain = String(text).replaceAll('**', '').replace(/(?<=\d),(?=\d{3}\b)/g, '');

  // Only receipts that actually have a metric_contract can be inspected for contract caliber
  const metricReceipts = evidence.filter(e => e.result_ref && e.metric_contract);
  const countReceipts = metricReceipts.filter(e => COUNT_UNITS.has(e.metric_contract?.unit) && !e.metric_contract?.ratio);

  for (const clause of plain.split(/[。；;\n]/)) {
    if (!clause.trim()) continue;

    // Subclause splitting by punctuation and contrastive conjunctions
    const subclauses = clause.split(/(?<=[，,])|(?=但是|但|然而|不过)/);

    // 1. Check count claim binding per subclause
    // Exclude calendar years from being misidentified as counted units
    for (const sub of subclauses) {
      for (const claim of sub.matchAll(/([一-鿿]{2,}?)(?:的)?(?:数量|总量|总数)\s*(?:为|是|共|合计|约|：|:)*\s*([\d.]+)/g)) {
        const value = Number(claim[2]);

        const beforeClaim = sub.slice(Math.max(0, claim.index - 25), claim.index);
        if (/(?:不能|不可|无法|尚未|未经|不是|并非|不代表|待确认|待核实)[^，,]{0,20}$/.test(beforeClaim)) continue;

        const subject = claim[1].slice(-2);
        const candidates = countReceipts.filter(e => e.facts?.some(f => Number.isFinite(f.value) && Math.abs(f.value - value) < 1e-8));
        const named = candidates.map(e => ({
          e,
          subjects: subjects(e.metric_contract?.name || e.metric_contract?.definition || '')
        })).filter(x => x.subjects.length);

        if (named.length && !named.some(x => x.subjects.includes(subject))) {
          issues.push('数值与统计对象不符：' + claim[0] + '引用的结果统计的是' + [...new Set(named.map(x => x.e.metric_contract?.name || '该指标'))].join('、') + '。不能将同一个数字替其他对象背书；按原问重查或明确该项未完成。');
        }
      }
    }

    // 2. Check "user-confirmed caliber" claim per subclause
    // Supports independent reference for mixed A unconfirmed / B confirmed contracts
    for (const sub of subclauses) {
      const confirmedMatch = sub.match(/(?:用户|业务方|管理员)(?:已)?确认(?:的)?(?:统计)?口径|口径(?:已经|已)?确认/);
      if (!confirmedMatch) continue;

      const before = sub.slice(Math.max(0, confirmedMatch.index - 25), confirmedMatch.index);
      if (/(?:不能|不可|无法|尚未|未经|不是|并非|不代表|待确认|待核实)[^，,]{0,15}$/.test(before)) continue;

      // Extract cited numbers or metric names in this subclause or the full clause
      const targets = claimReceipts(clause, metricReceipts);

      // If the targeted metric receipt is NOT confirmed, report issue
      const unconfirmedItem = targets.find(r => !isContractConfirmed(r.metric_contract));
      if (unconfirmedItem) {
        issues.push('本轮结果没有该来源、版本和范围的人工确认记录，不能称为“用户已确认口径”；按回执的配置及确认状态说明。');
      }
    }

    // 3. Check official metric validation / formal endorsement claim per subclause
    // Prohibits unratified endorsement: "正式指标数值本身已通过校验，可正常引用"
    // DOES NOT block legitimate arithmetic facts: "95÷118与库内计算一致", "数值与计算一致"
    for (const sub of subclauses) {
      const formalMatch = sub.match(/(?:正式指标(?:数值)?(?:本身)?(?:已|已经)?(?:通过校验|经校验)|可正常引用|作为正式指标)/);
      if (!formalMatch) continue;

      const before = sub.slice(Math.max(0, formalMatch.index - 25), formalMatch.index);
      if (/(?:不能|不可|无法|尚未|未经|不是|并非|不代表|未被|没有)[^，,]{0,15}$/.test(before)) continue;

      const targets = claimReceipts(clause, metricReceipts);

      const unconfirmed = targets.some(r => !isContractConfirmed(r.metric_contract));
      if (unconfirmed) {
        issues.push('本轮仅有同库计算或明细核对，业务口径尚待确认，不能称为“正式指标数值已通过校验”或“可正常引用”；按实际计算与确认状态说明，不给未核定数值作正式背书。');
      }
    }

    // 4. Check business ratification (核定 / 以核定值为准) endorsement per subclause
    for (const sub of subclauses) {
      const ratifyMatch = sub.match(/(?:指标(?:值)?|统计值|数值|数据)(?:表)?(?:已经|已)?核定|以核定值为准/);
      if (!ratifyMatch) continue;

      const before = sub.slice(Math.max(0, ratifyMatch.index - 25), ratifyMatch.index);
      if (/(?:不能|不可|无法|尚未|未经|不是|并非|不代表|未被|没有)[^，,]{0,15}$/.test(before)) continue;

      if (metricReceipts.length) {
        issues.push('本轮仅有计算结果，没有数值的业务核定记录；口径确认不能证明结果数值已经核定。请按实际来源说明，不添加核定背书。');
      }
    }
  }

  return [...new Set(issues)];
}
