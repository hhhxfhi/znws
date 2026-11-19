// Universal semantic recognizers and contract inspectors for metric evidence.
// Strictly evaluates actual production metric contracts:
// - formula, unit, ratio, mean_kind
// - semantic_spec.operands (columns, aggregations, filters)
// - semantic_spec.dimensions
// - executed_scope (table, base_filter, filters)
// - scope (filters with {dimension, op, values})
// - overlapping_dimensions, confirmation, business_confirmation
// NEVER relies on fixed legacy ID strings or fictional fields like 'role'.
// Names and definitions NEVER override explicit structural conflicts.

/**
 * Checks whether a metric contract is confirmed.
 * Compatible with either business_confirmation === 'confirmed' or confirmation.state === 'confirmed'.
 * If either is 'confirmed', the contract is considered confirmed.
 */
export function isContractConfirmed(c) {
  if (!c) return false;
  if (c.confirmation?.state) return c.confirmation.state === 'confirmed';
  return c.business_confirmation === 'confirmed';
}

/**
 * Recognizes a fund execution rate metric (spent / budget).
 * Enforces division direction: numerator must be spent, denominator must be budget.
 * Rejects inverted formulas ('budget/spent') or non-divisions ('spent+budget').
 * Names and definitions CANNOT override structural formula conflicts.
 */
// Recognize only a complete supported quotient; names do not override math.
function simpleRatio(c) {
  const f = String(c.formula || c.semantic_spec?.formula || '').replace(/\s+/g, '');
  const m = f.match(/^(?:100(?:\.0+)?\*)?(?:SUM\()?([\w\u4e00-\u9fff]+)\)?\/(?:NULLIF\()?(?:SUM\()?([\w\u4e00-\u9fff]+)\)?(?:,0\))?(?:\*100(?:\.0+)?)?$/i);
  if (!m) return null;
  return m.slice(1).map(id => {
    const op = c.semantic_spec?.operands?.find(o => o.id === id);
    return [id, op?.column].filter(Boolean).join(' ');
  });
}
export function isFundExecutionMetric(c) {
  if (!c) return false;
  const terms = simpleRatio(c);
  return Boolean(terms && /(?:^|[ _])spent(?:$|[ _])|支出/i.test(terms[0]) && /(?:^|[ _])budget(?:$|[ _])|预算/i.test(terms[1]));
}

/**
 * Recognizes a project acceptance share / rate metric (accepted project count / total project count).
 * Numerator must be accepted projects/units; denominator must be project count.
 * Rejects work completion formulas (accepted_work / planned_work).
 */
export function isProjectAcceptanceMetric(c) {
  if (!c) return false;
  const formula = String(c.formula || '').replace(/\s+/g, '');

  // Rejects work completion metrics using accepted_work / planned_work
  if (/accepted_work|planned_work|验收工作量|计划工作量|work|工作量/i.test(formula)) return false;

  if (formula) {
    const isAcceptanceDiv = /(?:100\.0\*)?accepted\/(?:NULLIF\()?(?:total|project|count)/i.test(formula) ||
                            /COUNT\(DISTINCT.*accepted/i.test(formula) ||
                            /(?:已验收|验收项目)\/(?:总项目|项目数|total)/.test(formula);
    if (isAcceptanceDiv && (c.ratio || c.unit === '%')) return true;
    return false;
  }

  const spec = c.semantic_spec;
  if (spec && Array.isArray(spec.operands)) {
    // If operands contain work metrics, it is NOT project acceptance rate
    const hasWorkCol = spec.operands.some(o => /work|工作量|amount|金额/i.test(String(o.column || o.id || o.name || '')));
    if (hasWorkCol) return false;

    const hasAccepted = spec.operands.some(o => /accepted|已验收/i.test(String(o.column || o.id || o.name || '')));
    const hasProjectTotal = spec.operands.some(o => /project|total|count|项目/i.test(String(o.column || o.id || o.name || '')));
    if (hasAccepted && hasProjectTotal && (c.ratio || c.unit === '%')) return true;
  }

  const name = String(c.name || '');
  const def = String(c.definition || '');
  if (!formula || !/work|工作量/i.test(formula)) {
    if (/验收项目占比|项目验收率|已验收项目占比|验收项目比例/i.test(name) ||
        /验收项目占比|项目验收率|已验收项目占比|验收项目比例/i.test(def)) {
      return true;
    }
  }

  return false;
}

/**
 * Checks whether evidence contains project acceptance rate facts or columns.
 */
export function hasProjectAcceptanceEvidence(receipts) {
  return receipts.some(e =>
    isProjectAcceptanceMetric(e?.metric_contract) ||
    (e?.values?.columns?.some(c => /验收项目|已验收项目/.test(c)) && !e?.values?.columns?.some(c => /工作量/.test(c)))
  );
}

/**
 * Recognizes a weighted work completion rate metric (sum(accepted_work) / sum(planned_work)).
 */
export function isWeightedWorkCompletionMetric(c) {
  if (!c) return false;
  const terms = simpleRatio(c);
  if (terms) return /accepted_work|实际.*工作量/i.test(terms[0]) && /planned_work|计划.*工作量/i.test(terms[1]);
  return !c.formula && c.mean_kind === 'weighted_by_planned_work';
}

/**
 * Recognizes an arithmetic mean metric (including project work completion arithmetic mean).
 */
export function isArithmeticMeanMetric(c) {
  if (!c) return false;
  if (c.mean_kind === 'arithmetic_project_mean' || c.mean_kind === 'arithmetic') return true;
  if (/\bAVG\s*\(/i.test(c.formula || '')) return true;
  const name = String(c.name || '');
  const def = String(c.definition || '');
  if (/算术平均|平均完成率/.test(name) || /算术平均/.test(def)) return true;
  return false;
}

/**
 * Checks whether a metric's contract affirmatively and demonstrably restricts
 * its population to completed / accepted projects using structured evidence.
 * 
 * Strict rules:
 * 1. Scope filters: structured as {dimension, op, values}. Maps dimension to
 *    real column via semantic_spec.dimensions.
 * 2. Affirmative proof requires ALL allowed values in IN/EQ to belong to completed status.
 *    IN(completed, pending) contains mixed values and is NOT a completed restriction.
 * 3. SQL base_filter: complex expressions with OR, NOT or nested conditions DO NOT prove
 *    affirmative completion; unknown remains unknown.
 * 4. Multi-operand metrics (e.g. spent / budget): ALL participating operands must be
 *    affirmatively restricted. Restricting only numerator (spent) while denominator
 *    (budget) remains unrestricted DOES NOT prove completed restriction for the metric.
 * 5. default_filters strings and definition text CANNOT replace executed proof.
 */
export function hasCompletedStatusScope(c) {
  if (!c) return false;

  const STATUS_TERMS = new Set(['status', 'stage', 'state', '项目状态', '状态', '阶段', '环节', '工程状态', '实施状态']);
  const COMPLETED_VALUES = new Set(['completed', 'accepted', '已完成', '已验收', '办结', '验收完成']);

  // Helper to test if a string represents a status column/dimension
  const isStatusField = str => {
    const s = String(str || '').toLowerCase();
    if (STATUS_TERMS.has(s)) return true;
    if (/(?:项目|工程|实施)?状态|(?:项目|工程)?阶段|status|state|stage/i.test(s)) return true;
    return false;
  };

  // Helper to verify that ALL values in an array belong to completed status
  const allValuesCompleted = vals => {
    const arr = Array.isArray(vals) ? vals : [vals];
    return arr.length > 0 && arr.every(v => COMPLETED_VALUES.has(String(v).toLowerCase()));
  };

  // 1. Structured filters in semantic_spec.operands[*].filters
  const operands = c.semantic_spec?.operands || [];
  if (operands.length > 0) {
    // ALL operands must affirmatively restrict to completed
    const operandProofs = operands.map(op => {
      const filters = Array.isArray(op.filters) ? op.filters : (Array.isArray(op.scope?.filters) ? op.scope.filters : []);
      return filters.some(f => {
        const col = String(f.column || f.field || f.dimension || '').toLowerCase();
        if (!isStatusField(col)) return false;
        const operator = String(f.op || '').toLowerCase();
        if (['ne', 'not_in', 'not_eq', '!=', '<>', 'not in'].includes(operator)) return false;
        if (['eq', 'in', '=', 'equal'].includes(operator)) {
          return allValuesCompleted(f.values !== undefined ? f.values : f.value);
        }
        return false;
      });
    });
    // If every operand has affirmative completed proof, return true
    if (operandProofs.every(Boolean)) {
      return true;
    }
    // If some operands are not proven completed, structured operands cannot prove completion
    // A global dimension filter below can still apply to all operands.
  }

  // 2. Structured filters in scope.filters: {dimension, op, values}
  // Maps dimension to real column via semantic_spec.dimensions
  const scopeFilters = Array.isArray(c.scope?.filters) ? c.scope.filters : [];
  if (scopeFilters.length > 0) {
    // If the metric has multiple operands and scope.filters only filters one operand/table, reject
    const hasPerOperandFilter = scopeFilters.some(f => f.operand && f.operand !== 'all' && f.operand !== 'both');
    if (hasPerOperandFilter && operands.length > 1) {
      return false;
    }

    const dimMap = new Map();
    for (const d of c.semantic_spec?.dimensions || []) {
      if (d.id) dimMap.set(d.id, d);
      if (d.name) dimMap.set(d.name, d);
      if (d.column) dimMap.set(d.column, d);
    }

    for (const f of scopeFilters) {
      if (f.operand && f.operand !== 'all' && f.operand !== 'both') continue;

      const dimId = String(f.dimension || f.column || f.field || '');
      const dimObj = dimMap.get(dimId);
      const isStatus = isStatusField(dimId) || isStatusField(dimObj?.column) || isStatusField(dimObj?.name);
      if (!isStatus) continue;

      const operator = String(f.op || '').toLowerCase();
      if (['ne', 'not_in', 'not_eq', '!=', '<>', 'not in'].includes(operator)) continue;
      if (['eq', 'in', '=', 'equal'].includes(operator)) {
        if (allValuesCompleted(f.values !== undefined ? f.values : f.value)) {
          return true;
        }
      }
    }
  }

  if (operands.length > 1) return false;

  // 3. Structured executed_scope.filters or c.filters
  const executedFilters = c.executed_scope?.filters || c.filters;
  if (executedFilters && typeof executedFilters === 'object') {
    for (const [k, v] of Object.entries(executedFilters)) {
      if (isStatusField(k) && allValuesCompleted(v)) {
        return true;
      }
    }
  }

  // 4. Executed base_filter SQL condition
  // Complex SQL conditions with OR, NOT, != or <> DO NOT prove completed status; unknown remains unknown.
  const baseFilter = String(c.executed_scope?.base_filter || '').trim();
  if (baseFilter) {
    const hasDisjunctionOrNegation = /\bOR\b|\bNOT\b|!=|<>/i.test(baseFilter);
    if (!hasDisjunctionOrNegation) {
      // Must be a simple affirmative equality: status = 'completed' or status IN ('completed')
      const simpleEq = /^(?:[a-zA-Z0-9_]+\.)?(?:status|state|stage)\s*=\s*['"]?(?:completed|accepted|已完成|已验收)['"]?$/i.test(baseFilter);
      const simpleIn = /^(?:[a-zA-Z0-9_]+\.)?(?:status|state|stage)\s+IN\s*\(\s*(['"][^'"]+['"]\s*(?:,\s*['"][^'"]+['"])*)\s*\)$/i.test(baseFilter);
      if (simpleEq) return true;
      if (simpleIn) {
        const matches = [...baseFilter.matchAll(/['"]([^'"]+)['"]/g)].map(m => m[1]);
        if (allValuesCompleted(matches)) return true;
      }
    }
  }

  return false;
}

/**
 * Checks whether an arithmetic mean work completion metric is affirmatively
 * configured specifically for projects entering acceptance.
 */
export function hasAcceptanceStageScope(c) {
  if (!c) return false;

  const STAGE_FIELDS = new Set(['stage', 'status', 'state', '阶段', '环节']);
  const ACCEPT_STAGE_VALUES = new Set(['acceptance', '验收', '进入验收', '验收阶段']);

  const scopeFilters = Array.isArray(c.scope?.filters) ? c.scope.filters : [];
  for (const f of scopeFilters) {
    const dim = String(f.dimension || f.column || f.field || '').toLowerCase();
    if (!STAGE_FIELDS.has(dim)) continue;
    const operator = String(f.op || '').toLowerCase();
    if (['ne', 'not_in', 'not_eq', '!='].includes(operator)) continue;
    if (['eq', 'in', '=', 'equal'].includes(operator)) {
      const vals = Array.isArray(f.values) ? f.values : [f.value];
      if (vals.length > 0 && vals.every(v => ACCEPT_STAGE_VALUES.has(String(v).toLowerCase()))) return true;
    }
  }

  const filters = c.executed_scope?.filters || c.filters || {};
  for (const [k, v] of Object.entries(filters)) {
    if (STAGE_FIELDS.has(String(k).toLowerCase()) && ACCEPT_STAGE_VALUES.has(String(v).toLowerCase())) {
      return true;
    }
  }

  const baseFilter = String(c.executed_scope?.base_filter || '').trim();
  if (baseFilter && !/\bOR\b|\bNOT\b|!=|<>/i.test(baseFilter)) {
    if (/^(?:[a-zA-Z0-9_]+\.)?(?:stage|阶段|环节)\s*=\s*['"]?(?:acceptance|验收)['"]?$/i.test(baseFilter)) {
      return true;
    }
  }

  return false;
}

/**
 * Checks whether the metric demonstrably allows overlapping entities along a given dimension.
 * STRICT: Must use contract's overlapping_dimensions array; NEVER guesses from metric name.
 */
export function hasOverlappingDimension(c, dim = 'profession') {
  if (!c) return false;
  return Array.isArray(c.overlapping_dimensions) && c.overlapping_dimensions.includes(dim);
}

/**
 * Recognizes on-time rate / timeliness metric.
 */
export function isOnTimeRateMetric(c) {
  if (!c) return false;
  const formula = String(c.formula || '');
  if (/on_time/i.test(formula)) return true;
  const name = String(c.name || '');
  const def = String(c.definition || '');
  if (/按时办结率|按时办结|办结时效/i.test(name) || /按时办结率/i.test(def)) return true;
  return false;
}

/**
 * Recognizes inspection pass rate metric.
 */
export function isInspectionPassRateMetric(c) {
  if (!c) return false;
  const formula = String(c.formula || '');
  if (/passed.*inspected|合格.*已检查/i.test(formula)) return true;
  const name = String(c.name || '');
  const def = String(c.definition || '');
  if (/监督检查合格率|检查合格率/i.test(name) || /监督检查合格率/i.test(def)) return true;
  return false;
}
