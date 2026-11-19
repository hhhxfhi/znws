// Guard against delivering unaggregated detail receipts (with prose subtotals)
// when the user asked for a grouped summary by business dimension(s).

const DETAIL_REQUEST = /明细|流水|逐笔|逐条|逐单|清单|每笔|每条|每一笔|每一条|合同号|订单号|单号|流水号|凭证号|发票号|运单号|批次号|记录号|明细表|(?:记录|退款|订单|合同|交易|费用|采购|明细)列表/;
const NON_SUMMABLE_REQUEST = /平均|均值|客单价|人均|单价|中位数|方差|标准差|率|占比|比例|百分比|%|％|余额|库存|存量|期末|期初/;
const GROUP_CUE = /(?:各|每个|各个)(?:门店|店|科室|部门|客户|供应商|单位|机构|地区|地市|城市|区县|省份|仓库|渠道|项目|产品|类别|类型|月|季度|年)|(?:按|分|依照|根据)[\p{Script=Han}a-zA-Z0-9_、和与及]{1,24}?(?:汇总|统计|计算|分组|列出|列出来|聚合|求和)/u;
const ADDITIVE_REQUEST = /金额|总额|总金额|退款|费用|收入|支出|花费|成本|利润|销售额|营业额|回款|采购额|付款|收款|补贴|预算|拨付|投入|数量|件数|总量|总数|总计|合计|多少元|多少钱|多少万元|多少亿元|多少件|多少吨|多少台|多少套|有多少笔|多少笔/;

const DETAIL_ID_COL = /(?:合同|订单|流水|凭证|发票|运单|批次|记录|明细|交易|业务|单据)(?:号|编号|id|no|code)|(?:^|_)(?:contract|order|refund|voucher|invoice|record|detail|tx|trans|flow|serial|seq)_(?:id|no|code|num)$|^(?:id|no|code|seq|remark|note|备注|说明|创建时间|更新时间|created_at|updated_at)$/i;
const ENTITY_ID_COL = /(?:门店|店铺|分店|科室|部门|客户|供应商|单位|机构|仓库|渠道|项目|地区|区域)\s*(?:id|编号|代码|编码)|(?:^|_)(?:store|shop|branch|dept|department|customer|client|supplier|vendor|org|unit|warehouse|channel|project|region|city)_(?:id|code|no)$/i;
const DATE_DETAIL_COL = /(?:日期|时间|时刻|时间戳|_date$|_at$|_time$|^date$|^time$)/i;
const TIME_GROUP_CUE = /按\s*(?:日|天|周|月|季度|年|时间|日期)|每\s*(?:日|天|周|月|季度|年)|各\s*(?:日|月|季度|年)|逐\s*(?:日|月|年)|分\s*(?:日|月|年)/;
const NUMERIC_TEXT = /^\s*-?\d+(?:\.\d+)?\s*$/;

const isNumericCell = v => (typeof v === 'number' && Number.isFinite(v)) || (typeof v === 'string' && NUMERIC_TEXT.test(v));

export function hasGroupedSummaryIntent(userText = '') {
  const text = String(userText || '').trim();
  if (!text || DETAIL_REQUEST.test(text) || NON_SUMMABLE_REQUEST.test(text)) return false;
  return GROUP_CUE.test(text) && ADDITIVE_REQUEST.test(text);
}

function hasDuplicateGroupKeys(receipt, userText) {
  const cols = receipt?.values?.columns;
  const rows = receipt?.values?.rows;
  if (!Array.isArray(cols) || !Array.isArray(rows) || rows.length < 2) return false;
  const timeGrouped = TIME_GROUP_CUE.test(String(userText || ''));

  const groupIndexes = [];
  const detailIdIndexes = [];
  let hasMeasure = false;

  for (let i = 0; i < cols.length; i++) {
    const col = String(cols[i] || '');
    if (DETAIL_ID_COL.test(col) && !(col && String(userText || '').includes(col))) {
      detailIdIndexes.push(i);
      continue;
    }
    if (DATE_DETAIL_COL.test(col) && !timeGrouped) {
      detailIdIndexes.push(i);
      continue;
    }
    if (ENTITY_ID_COL.test(col)) {
      groupIndexes.push(i);
      continue;
    }
    const nonNull = rows.map(r => r?.[i]).filter(v => v !== null && v !== undefined && v !== '');
    if (nonNull.length && nonNull.every(isNumericCell)) {
      hasMeasure = true;
      continue;
    }
    groupIndexes.push(i);
  }

  if (!groupIndexes.length || !hasMeasure) return false;
  const keys = new Set(rows.map(r => JSON.stringify(groupIndexes.map(i => r?.[i] ?? null))));
  if (keys.size >= rows.length) return false;
  return detailIdIndexes.length > 0 || receipt?.answer_contract?.kind !== 'sql_aggregate';
}

function unobservedZeroIssues(text, evidence, userText) {
  if (!hasGroupedSummaryIntent(userText)) return [];
  const observedLabels = new Set();
  for (const e of evidence) {
    if (e?.purpose === 'explore' || e?.status !== 'succeeded') continue;
    for (const row of e?.values?.rows || []) {
      if (!Array.isArray(row)) continue;
      for (const cell of row) {
        if (typeof cell === 'string' && cell.trim()) observedLabels.add(cell.trim());
      }
    }
    for (const f of e?.facts || []) {
      for (const v of Object.values(f?.dimensions || {})) {
        if (typeof v === 'string' && v.trim()) observedLabels.add(v.trim());
      }
    }
  }
  if (!observedLabels.size) return [];

  const zeroClaim = /([\p{Script=Han}A-Za-z0-9]{1,20}(?:门店|店|科室|部门|客户|供应商|单位|公司|仓库|渠道|项目))[^。；;\n]{0,16}?(?:为|是|共|合计|退款|金额)?\s*0(?:\.0+)?\s*(?:元|万元|亿元|件|笔|个)/gu;
  const issues = [];
  for (const clause of String(text || '').split(/[。；;\n]/)) {
    if (/无[^。；;\n]{0,12}记录|没有[^。；;\n]{0,12}记录|未查询到|未发生|未登记|留空/.test(clause)) continue;
    for (const m of clause.matchAll(zeroClaim)) {
      const entity = m[1].trim();
      if (!observedLabels.has(entity) && ![...observedLabels].some(l => l.includes(entity) || entity.includes(l))) {
        issues.push('查询结果中没有「' + entity + '」的记录，不能将未出现或无记录的分组写成 0；请仅报告结果中实际存在的分组，或如实说明无相关记录。');
        break;
      }
    }
  }
  return issues;
}

export function groupSummaryIssues(text, evidence, userText = '') {
  const issues = [];
  if (hasGroupedSummaryIntent(userText)) {
    const formal = (evidence || []).filter(
      e => e?.purpose !== 'explore' && e?.status === 'succeeded' && e?.values?.complete === true && Array.isArray(e?.values?.rows)
    );
    const hasCleanSummary = formal.some(
      e => e?.answer_contract?.kind === 'sql_aggregate' && !hasDuplicateGroupKeys(e, userText)
    );
    if (!hasCleanSummary && formal.some(e => hasDuplicateGroupKeys(e, userText))) {
      issues.push(
        '用户要求按业务维度汇总，当前正式结果仍是未聚合的流水明细（存在重复分组或未请求的明细编号列），导致正文与图卡口径不一致。请按所问维度在 SQL 中直接 GROUP BY 聚合（如 SUM(...)，保留全 NULL 与真实 0，不附带明细单号/合同号；若涉及多表关联请先核对去重口径再聚合），并用同一份正式汇总结果统一输出正文、表格和图卡。'
      );
    }
  }
  issues.push(...unobservedZeroIssues(text, evidence, userText));
  return [...new Set(issues)];
}
