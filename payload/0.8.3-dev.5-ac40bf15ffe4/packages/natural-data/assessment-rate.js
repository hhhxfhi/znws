const normalized = text => String(text || '').normalize('NFKC').replace(/[\s。!?,;]+/g, '').toLowerCase();

export function assessedRateObservation(result, request = result?.result_lineage?.request_ref,
  session = result?.result_lineage?.session_id) {
  const proof = result?.validation?.assessment_rate;
  const lineage = result?.result_lineage;
  const values = result?.values;
  if (result?.status !== 'succeeded' || result.truncated || !result.result_ref || result.receipt?.read_only !== true
    || result.validation?.state === 'conflict' || lineage?.purpose !== 'answer' || !request || !session
    || lineage.request_ref !== request || lineage.session_id !== session || proof?.schema_version !== 1
    || proof.request_ref !== request || proof.source_id !== result.receipt.source_id || proof.source_id !== lineage.source_id
    || !/^(?:19|20)\d{2}$/.test(proof.year) || !/^[\p{Script=Han}]{2,22}$/u.test(proof.label)
    || !['合格', '通过'].includes(proof.positive_label) || !proof.question_clause
    || values?.complete !== true || values.rows?.length !== 1 || result.row_count !== 1
    || !Array.isArray(values.columns)) return null;
  const indices = ['numerator', 'denominator', 'rate'].map(key => values.columns.indexOf(proof.columns?.[key]));
  if (indices.includes(-1) || new Set(indices).size !== 3) return null;
  const [passed, assessed, rate] = indices.map(index => values.rows[0][index]);
  if (![passed, assessed].every(Number.isSafeInteger) || passed < 0 || assessed < passed
    || (assessed === 0 ? rate !== null : !Number.isFinite(rate) || Math.abs(rate - 100 * passed / assessed) > .00501)) return null;
  return {proof, passed, assessed, rate};
}

export function matchesAssessmentClause(observation, clause) {
  return normalized(observation.proof.question_clause) === normalized(clause);
}

export function assessedRateText({proof, passed, assessed, rate}) {
  return assessed
    ? `${proof.year} 年${proof.label} **${rate.toFixed(2)}%**（${proof.positive_label} ${passed} 条，已判定 ${assessed} 条）。`
    : `${proof.year} 年没有已判定的记录，${proof.label}暂无法计算。`;
}

export function assessedRateAnswerIssues(text, evidence) {
  for (const {proof, rate} of evidence.map(result => assessedRateObservation(result)).filter(Boolean)) {
    if (rate === null) continue;
    const lines = String(text).replaceAll('**', '').split(/[。！？!？\n]/);
    const matching = lines.filter(line => line.includes(proof.label) || line.includes(proof.positive_label+'率'));
    const percentages = matching.flatMap(line => [...line.matchAll(/(-?\d+(?:\.\d+)?)\s*[%％]/g)].map(match => Number(match[1])));
    if (!percentages.some(value => Math.abs(value - rate) < .00501))
      return ['交付缺项：按同一期间已判定记录交付'+proof.label+'及分子分母。'];
  }
  return [];
}
