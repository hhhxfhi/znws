"""业务口径 Excel 模板与批量导入：按指标编码比对，校验后写入项目草稿（发布后问答才使用）。

0.7.0 由测绘版（next）移植。openpyxl 随插件分发在 engine/_vendor，客户环境已安装时优先用已安装的版本。
"""
from __future__ import annotations
import base64
import copy
import io
import json
import re
import sys
import threading
import time
import uuid
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import iterparse, ParseError
from pathlib import Path

COLS = [('code', '指标编码(id)', True), ('name', '指标名称', True), ('definition', '业务描述', True), ('unit', '单位(unit)', True), ('table', '主表表名(table)', True),
        ('join_table', '关联表(table_join)', False), ('join_keys', '关联字段/关联条件(join_keys)', False), ('caliber', '统计口径', True), ('time_dims', '时间维度', True),
        ('region_dims', '区域维度', True), ('question', '问题示例', False), ('sql', 'SQL示例', False), ('coverage', '覆盖期间(coverage)', True), ('source', '数据源', False), ('parent', '上级指标编码', False), ('time_format', '时间字段格式(time_format)', False), ('metric_config', '结构化口径草案(metric_config)', False)]
ALIASES = {'问题示例(自然语言)': 'question', '覆盖期间': 'coverage'}
CODE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
MAX_EXCEL_ROWS = 10000
MAX_EXCEL_COLUMNS = 128
MAX_EXCEL_CELLS = 200000
MAX_EXCEL_EXPANDED_BYTES = 32 * 1048576
ALWAYS = {'code', 'name'}


def schema_of(project):
    """按项目「字段方案」得到本客户使用的标准列（叫法、别名、是否必填）和扩展列。"""
    sch = (project.get('draft') or {}).get('info_schema') or {}
    over = {f.get('key'): f for f in sch.get('fields', []) if isinstance(f, dict)}
    cols = []
    for key, label, req in COLS:
        o = over.get(key, {})
        use = 'required' if key in ALWAYS else (o.get('use') or ('required' if req else 'optional'))
        if use == 'off': continue
        cols.append({'key': key, 'label': (o.get('label') or label).strip(), 'std': label, 'required': use == 'required', 'aliases': [a for a in o.get('aliases', []) if isinstance(a, str) and a.strip()]})
    ext = [{'key': e['key'], 'label': e['label'], 'in_qa': bool(e.get('in_qa'))} for e in sch.get('extensions', []) if isinstance(e, dict) and e.get('key') and e.get('label')]
    return cols, ext


def _wb():
    try:
        import openpyxl
    except ImportError:
        vendor = str(Path(__file__).resolve().parents[1] / '_vendor')
        if vendor not in sys.path:
            sys.path.append(vendor)
        import openpyxl
    return openpyxl


def template(project, sources, prefill):
    op = _wb()
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = op.Workbook(); ws = wb.active; ws.title = '业务口径'
    req, opt = PatternFill('solid', fgColor='DCE6FF'), PatternFill('solid', fgColor='F2F3F5')
    cols, ext = schema_of(project)
    extf = PatternFill('solid', fgColor='FFF2CC')
    ws.append([c['label'] for c in cols] + [e['label'] for e in ext])
    for i, c in enumerate(cols + ext, 1):
        cell = ws.cell(1, i); cell.font = Font(bold=True); cell.fill = extf if c in ext else req if c['required'] else opt; cell.alignment = Alignment(wrap_text=True, vertical='top')
    draft = project['draft']; srcs = {s['id']: s for s in sources}
    if prefill:
        ex = {}
        for e in draft.get('examples', []): ex.setdefault(e.get('metric_code'), e)
        for t in draft.get('terms', []):
            cfg = t.get('config') or t.get('proposed_config') or {}
            code = t.get('code') or cfg.get('id')
            if not code: continue
            ops = cfg.get('operands') or [{}]; cov = sorted(p for p in cfg.get('coverage', []) if len(p) == 7) or sorted(p for p in cfg.get('coverage', []) if len(p) == 4)
            rel = (cfg.get('relations') or [{}])[0]
            e = ex.get(code, {})
            vals = {'code': code, 'name': t['name'], 'definition': t.get('definition', ''), 'unit': cfg.get('unit', ''), 'table': ops[0].get('table', ''), 'join_table': rel.get('to_table', ''),
                    'join_keys': f"主表.{','.join(rel.get('from_columns', []))} = 关联表.{','.join(rel.get('to_columns', []))}" if rel.get('to_table') else '',
                    'caliber': t.get('caliber_text') or describe(cfg), 'time_dims': t.get('time_dims') or ('月度/年度' if cfg.get('time_mode') == 'period' else '全量'), 'region_dims': t.get('region_dims') or '、'.join(d['id'] for d in cfg.get('dimensions', [])),
                    'question': e.get('question', ''), 'sql': e.get('logical_sql', ''), 'coverage': f'{cov[0]}~{cov[-1]}' if cov else '', 'source': srcs.get(cfg.get('source_id'), {}).get('name', ''), 'parent': t.get('parent_code', ''), 'time_format': ops[0].get('time_format') or ''}
            if t.get('status') == 'review': vals.update(t.get('excel_input') or {})
            if any(o.get('latest') for o in ops):
                latest=ops[0].get('latest') or {}
                vals.update(table=latest.get('entity_table',ops[0].get('table','')),join_table=ops[0].get('table',''),
                    join_keys=' AND '.join('主表.'+a+'=关联表.'+b for a,b in zip(latest.get('entity_key',[]),latest.get('report_key',[]))),
                    metric_config=json.dumps(cfg,ensure_ascii=False,separators=(',',':')))
            ws.append([vals.get(c['key'], '') for c in cols] + [(t.get('attributes') or {}).get(x['key'], '') for x in ext])
    for i, w in enumerate([14, 26, 40, 8, 18, 16, 24, 48, 12, 16, 30, 48, 18, 14, 14], 1): ws.column_dimensions[op.utils.get_column_letter(i)].width = w
    ws.freeze_panes = 'C2'
    d = wb.create_sheet('维度定义'); d.append(['维度值', '用法（不分组/分组/筛选）', '表', '字段', '筛选条件'])
    for x in draft.get('dimensions', []): d.append([x.get('value'), {'none': '不分组', 'group': '分组', 'filter': '筛选'}.get(x.get('usage'), ''), x.get('table', ''), x.get('column', ''), x.get('filter', '')])
    t = wb.create_sheet('可选表字段'); t.append(['表名', '字段', '字段类型', '中文业务名'])
    for sid in draft['source_ids']:
        for tb in srcs.get(sid, {}).get('tables', []):
            if tb['name'] not in draft['tables'].get(sid, []): continue
            for c in tb['columns']: t.append([tb['name'], c['name'], c.get('type', ''), c.get('label', '')])
    n = t.max_row
    tpos = next((i for i, c in enumerate(cols, 1) if c['key'] == 'table'), None)
    if tpos:
        letter = op.utils.get_column_letter(tpos)
        dv = DataValidation(type='list', formula1=f"='可选表字段'!$A$2:$A${max(2, n)}", allow_blank=True); ws.add_data_validation(dv); dv.add(f'{letter}2:{letter}2000')
    h = wb.create_sheet('填表说明'); h.column_dimensions['A'].width = 22; h.column_dimensions['B'].width = 110
    for row in [('导入行为', '按「指标编码」比对：已存在→更新（版本号自动递增）；新编码→新增；文件中没有的指标默认保留。结果写入项目草稿，在配置管理里校验发布后问答才使用。'),
                ('统计口径', "按“统计范围：…；计算公式：…；筛选条件：…”书写。字段名写在括号里，如 签批时间(pzsj)。公式支持 COUNT(*)、COUNT(字段)、COUNT(DISTINCT 字段)、SUM/AVG/MIN/MAX(字段)及两个聚合相除，可写 ×100。筛选支持 AND、比较和 IN。比率带业务筛选或文字筛选固定统计时间字段时只登记待复核文字。SQL 示例限同一主表的单值聚合；示例日期范围随查询期间替换，保留其他业务筛选；未知字段或无法完整解析的口径仅作为待复核文字导入，不自动替换成其他计算。"),
                ('文件范围', '上传文件不超过 5 MB。每张表最多 10000 行、128 列、20 万格，解压后合计不超过 32 MB。请清除远端空白格式或拆分文件。表头中的标准字段、别名和扩展字段均不得重复。'),
                ('覆盖期间', '已确认数据完整可查的期间，如 2023-01~2026-09 或 2023~2025。为空的行会被拒绝。'),
                ('区域维度', '填写「维度定义」表中的维度值（如 省级、市级、省外），多个用“/”分隔。'),
                ('一级汇总行', '没有 SQL 示例、单位为复合单位（如 件/家）且有下级编码的行，登记为指标分组，不直接查询。'),
                ('已统计值', '外部系统已核定的统计值可用 SINGLE_VALUE(字段)：同一期间和筛选范围必须恰好一条，重复或缺失不自动求和/取最大值。只支持直接取值，不与其他公式混算；原始计算口径需引用数据来源说明。'),
                ('最新关联报送', '结构化口径草案(metric_config)可填写完整指标 JSON，操作数 latest 声明实体表、关联键、日期格式、截止范围和并列/缺报规则。导入只保存待复核草案；请在指标口径的高级配置中核对并确认。不会自动执行 SQL 示例、推断业务字段或扩大授权。'),
                ('时间字段格式', '按实际字段编码填写 date、yyyy 或 yyyymm；留空保持日期格式。yyyy 只能使用已确认的完整年度覆盖，部分月份不能自动当全年。'),
                ('校验', '编码格式、文件内重复、授权表与字段、覆盖期间、计算公式、关联键、期间与 SQL 年份一致、业务描述、维度取值、关联唯一键、SQL 只读与表范围、同名不同码等。拒绝及待完善的行写入校验回执。')]:
        h.append(list(row))
        for c in h[h.max_row]: c.alignment = Alignment(wrap_text=True, vertical='top')
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def describe(cfg):
    if not cfg: return ''
    ops = cfg.get('operands', [])
    def agg(o):
        name = o.get('aggregation', 'sum').upper()
        col = o.get('column')
        return f'COUNT(DISTINCT {col})' if name == 'DISTINCT' else f"{name}({col or '*'})"
    def literal(v):
        if isinstance(v, str): return "'" + v.replace("'", "''") + "'"
        if isinstance(v, bool): return 'TRUE' if v else 'FALSE'
        return str(v)
    def predicate(f):
        vals = [literal(v) for v in f['values']]
        if f['op'] == 'in': return f["column"]+' IN ('+', '.join(vals)+')'
        return f['column']+{'eq':'=', 'ne':'<>', 'gt':'>', 'gte':'>=', 'lt':'<', 'lte':'<='}[f['op']]+vals[0]
    if not ops: return ''
    # An arbitrary multi-operand formula (including each operand's own filters) cannot
    # be represented by the sheet's single filter cell. Show its actual definition for review.
    if len(ops) != 1 or cfg.get('formula') != ops[0].get('id'):
        return '需在指标口径中复核；原公式：'+str(cfg.get('formula',''))+'；操作数：'+json.dumps(ops,ensure_ascii=False)
    o = ops[0]
    filters = ' AND '.join(predicate(f) for f in o.get('filters', []))
    scope = '('+o['time_column']+')在统计期内' if o.get('time_column') else '全量'
    return f"统计范围：{scope}；计算公式：{agg(o)}；筛选条件：{filters or '无'}"


def _periods(text):
    text = str(text or '').strip().replace('～', '~').replace('至', '~').replace('—', '~').replace('－', '~')
    m = re.fullmatch(r'(\d{4})(?:-(\d{2}))?\s*~\s*(\d{4})(?:-(\d{2}))?', text)
    if not m: return None
    y1, m1, y2, m2 = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 12)
    if (y1, m1) > (y2, m2) or not (2000 <= y1 <= y2 <= 2099 and 1 <= m1 <= 12 and 1 <= m2 <= 12): return None
    months, y, mm = [], y1, m1
    while (y, mm) <= (y2, m2):
        months.append(f'{y}-{mm:02d}'); mm += 1
        if mm > 12: mm, y = 1, y + 1
    years = [str(yy) for yy in range(y1, y2 + 1) if sum(p.startswith(str(yy)) for p in months) == 12]
    return years + months


_QUOTED = r"""'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`"""
_NO_FILTER = ('', '无', '无筛选条件')


def _caliber_sections(text):
    """Read sheet headings without treating quoted SQL text as headings."""
    text = str(text or '')
    markers = [m for m in re.finditer(_QUOTED + r'|(统计范围|计算公式|筛选条件)\s*[：:]', text) if m[1]]
    result = {}
    for i, match in enumerate(markers):
        if match[1] in result: raise ValueError('统计口径中同一段出现多次，需人工复核')
        result[match[1]] = text[match.end():markers[i+1].start() if i+1 < len(markers) else None].strip(' ；;\n')
    return result


def _formula_placeholder(text):
    value = str(text or '').strip().strip('()（）').strip()
    return bool(re.fullmatch(r'(?:原(?:文档?|资料)(?:中)?\s*)?(?:未|没有|尚未)(?:提供|给出|明确|填写)(?:计算)?公式|待(?:补充|完善|确认)|不详|未知', value))


def _formula_diagnostics(formula, table, row_cols):
    """Explain compiler limits; never infer a physical column from a label."""
    import sqlglot
    from sqlglot import exp
    if not formula or _formula_placeholder(formula) or table not in row_cols: return []
    try:
        trees = sqlglot.parse(_sheet_expr(formula))
        if len(trees) != 1 or trees[0] is None: raise ValueError('multiple expressions')
        unknown = sorted({c.name for c in trees[0].find_all(exp.Column)} - row_cols[table][1])
        if unknown:
            return [('FORMULA_COLUMN_UNKNOWN', '公式中的名称未对应授权表物理字段：'+ '、'.join(unknown[:8])+'；请填写实际字段，计数对象须确认，不能自动改为 COUNT(*)')]
    except (ValueError, sqlglot.errors.SqlglotError):
        return [('FORMULA_EXPRESSION', '已经填写公式，但不是可完整解析的单一聚合或比率表达式；请明确本指标对应的字段和公式，不属于“未写公式”')]
    return []


def _sheet_expr(text, filters=False):
    # Field labels and separators are sheet syntax, never rewrite quoted SQL values.
    pattern = _QUOTED + r'|[\u3400-\u9fff]+[（(]([a-zA-Z_][\w.]*)[)）]|[；;÷×]'
    def replace(m):
        if m[1]: return m[1]
        if filters and m[0] in ('；', ';'): return ' AND '
        return {'÷': '/', '×': '*'}.get(m[0], m[0])
    return re.sub(pattern, replace, text)


def _conjuncts(node):
    from sqlglot import exp
    if isinstance(node, exp.Paren): yield from _conjuncts(node.this)
    elif isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    else: yield node


def _date_literal(node):
    from datetime import datetime
    from sqlglot import exp
    if isinstance(node, exp.Cast) and node.to.sql() in ('DATE','TIMESTAMP','DATETIME'): node = node.this
    if isinstance(node, exp.StrToDate):
        fmt = node.args.get('format')
        if not isinstance(fmt,exp.Literal) or fmt.this != '%Y-%m-%d': return False
        node = node.this
    if not isinstance(node, exp.Literal) or not node.is_string: return False
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)?', node.this): return False
    try: datetime.fromisoformat(node.this)
    except ValueError: return False
    return True


def _sample_parts(sql, table, dialect, time_col, catalog=None):
    """Accept a single scalar SELECT only; don't turn grouped/joined data into totals."""
    import sqlglot
    from sqlglot import exp
    statements = sqlglot.parse(sql, read=dialect)
    tree = statements[0]
    if (len(statements) != 1 or not isinstance(tree, exp.Select) or len(tree.expressions) != 1
            or any(v for k, v in tree.args.items() if k not in ('expressions', 'from', 'from_', 'where'))):
        raise ValueError('SQL 示例不是可完整表达的单表单值聚合')
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        raise ValueError('SQL 示例的来源不唯一')
    rel = tables[0]
    source = tree.args.get('from') or tree.args.get('from_')
    def resolved(identifier):
        if identifier.args.get('quoted'): return identifier.name
        return identifier.name.upper() if dialect == 'oracle' else identifier.name.lower()
    parts = tuple(resolved(part) for part in rel.parts)
    matches = [name for name in (catalog if catalog is not None else [table])
               if tuple(name.split('.')[-len(parts):]) == parts]
    if (not source or source.this is not rel or sum(1 for _ in tree.find_all(exp.Select)) != 1
            or not isinstance(rel.this, exp.Identifier) or matches != [table]
            or any(v for k, v in rel.args.items() if k not in ('this','db','catalog','alias'))
            or (rel.args.get('alias') and rel.args['alias'].args.get('columns'))):
        raise ValueError('SQL 示例与所填主表不一致或包含额外的表操作')
    for col in tree.find_all(exp.Column):
        alias = rel.args['alias'].this if rel.args.get('alias') else rel.this
        if col.db or col.catalog or (col.table and resolved(col.args['table']) != resolved(alias)):
            raise ValueError('SQL 示例字段不属于所填主表')
        col.this.set('this', resolved(col.this))
        col.set('table', None)
    expr = tree.expressions[0]
    if isinstance(expr, exp.Alias): expr = expr.this
    where = tree.args.get('where')
    terms = list(_conjuncts(where.this)) if where else []
    def sample_date(node):
        if not isinstance(node.this, exp.Column): return None
        if isinstance(node, exp.Between) and _date_literal(node.args['low']) and _date_literal(node.args['high']):
            return node.this.name
        if type(node) in (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ) and _date_literal(node.expression):
            return node.this.name
    dates = {sample_date(term) for term in terms} - {None}
    if not time_col:
        # A lone cutoff could instead be a permanent business rule.
        bounded = {sample_date(t) for t in terms if isinstance(t, exp.Between)}
        lower = {sample_date(t) for t in terms if type(t) in (exp.GT, exp.GTE)}
        upper = {sample_date(t) for t in terms if type(t) in (exp.LT, exp.LTE)}
        if dates and not (dates & (bounded | (lower & upper))):
            raise ValueError('孤立日期条件无法确定统计时间字段')
    if not time_col and len(dates) > 1: raise ValueError('SQL 示例涉及多个日期字段，请明确统计时间字段')
    time_col = time_col or next(iter(dates), '')
    kept = [t for t in terms if not time_col or sample_date(t) != time_col]
    return time_col, expr.sql(), ' AND '.join('('+t.sql()+')' for t in kept)


def parse_caliber(text, sql, table, dialect, catalog=None):
    """三段式文字与 SQL 示例相互补全。示例的期间由运行时提供，业务筛选必须完整保留。"""
    import sqlglot
    try: seg = _caliber_sections(text)
    except ValueError as error: return '', '', '', [str(error)]
    times = re.findall(r'[（(]([a-zA-Z_][\w]*)[)）]', seg.get('统计范围',''))
    if len(set(times)) > 1: return '', '', '', ['统计范围涉及多个字段，需人工复核']
    time_col = times[0] if times else ''
    formula = _sheet_expr(seg.get('计算公式',''))
    filt = _sheet_expr(seg.get('筛选条件',''), filters=True)
    if not sql: return time_col, formula, filt, []
    try:
        tc, sf, sw = _sample_parts(sql, table, dialect, time_col, catalog)
        def key(value):
            trees = sqlglot.parse(value)
            if len(trees) != 1 or trees[0] is None: raise ValueError('表达式不完整')
            return sorted(t.sql() for t in _conjuncts(trees[0]))
        if formula and key(formula) != key(sf): raise ValueError('所填公式与 SQL 示例不一致')
        if filt and (key(filt) if filt not in _NO_FILTER else []) != (key(sw) if sw else []):
            raise ValueError('所填筛选与 SQL 示例的业务条件不一致')
        return tc, formula or sf, filt or sw, ['已核对单表 SQL；示例期间随查询替换，保留业务筛选']
    except (ValueError, sqlglot.errors.SqlglotError) as e:
        return time_col, '', filt, ['SQL 示例不能完整编译：'+str(e)[:120]]


def _read_rows(raw):
    """Bound ZIP expansion and worksheet geometry before allocating workbook objects."""
    op = _wb()
    message = 'EXCEL_RESOURCE_LIMIT: 表格范围过大。每张表最多 10000 行、128 列、20 万格，解压后合计不超过 32 MB；请清除远端空白格式或拆分文件。'
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 1024 or sum(e.file_size for e in entries) > MAX_EXCEL_EXPANDED_BYTES:
                raise ValueError(message)
            for entry in entries:
                if not (entry.filename.startswith('xl/worksheets/') and entry.filename.endswith('.xml')):
                    continue
                row = 0
                cells = 0
                with archive.open(entry) as stream:
                    root = None
                    for event, node in iterparse(stream, events=('start', 'end')):
                        tag = node.tag.rsplit('}', 1)[-1]
                        if root is None: root = node
                        if event == 'end':
                            node.clear()
                            if tag == 'row': root.clear()
                            continue
                        if tag == 'dimension':
                            _, _, col, last_row = op.utils.cell.range_boundaries(node.attrib['ref'])
                            if col and last_row and (col > MAX_EXCEL_COLUMNS or last_row > MAX_EXCEL_ROWS or col * last_row > MAX_EXCEL_CELLS):
                                raise ValueError(message)
                        elif tag == 'row':
                            row = int(node.attrib.get('r', row + 1))
                            if not 1 <= row <= MAX_EXCEL_ROWS: raise ValueError(message)
                        elif tag == 'c':
                            cells += 1
                            ref = node.attrib.get('r')
                            if ref:
                                cell_row, col = op.utils.cell.coordinate_to_tuple(ref)
                                if (not 1 <= cell_row <= MAX_EXCEL_ROWS or col > MAX_EXCEL_COLUMNS
                                        or cell_row * col > MAX_EXCEL_CELLS):
                                    raise ValueError(message)
                            if cells > MAX_EXCEL_CELLS: raise ValueError(message)
        wb = op.load_workbook(io.BytesIO(raw), data_only=True, read_only=True, keep_links=False)
        try:
            ws = wb['业务口径'] if '业务口径' in wb.sheetnames else wb.worksheets[0]
            # Exporters can declare a smaller dimension than their actual cells.
            # The XML preflight already bounds every row and cell coordinate.
            ws.reset_dimensions()
            rows = []
            cells = 0
            for values in ws.iter_rows(values_only=True):
                cells += len(values)
                if len(rows) >= MAX_EXCEL_ROWS or len(values) > MAX_EXCEL_COLUMNS or cells > MAX_EXCEL_CELLS:
                    raise ValueError(message)
                rows.append(list(values))
            return rows
        finally:
            wb.close()
    except (BadZipFile, ParseError, KeyError, IndexError) as exc:
        raise ValueError('Excel 文件结构无效，请用表格软件另存为 .xlsx 后重新上传。') from exc


def parse(raw, project, sources, engine):
    op = _wb()
    rows = _read_rows(raw)
    if not rows: raise ValueError('表格为空')
    header = [str(x or '').strip() for x in rows[0]]
    cols, ext = schema_of(project)
    idx, ext_idx, unknown = {}, {}, {}
    for i, hname in enumerate(header):
        if not hname: continue
        hit = next((c['key'] for c in cols if hname in (c['label'], c['std'], c['std'].split('(')[0], c['label'].split('(')[0], *c['aliases']) or ALIASES.get(hname) == c['key']), None)
        if hit:
            if hit in idx:
                raise ValueError(f'EXCEL_DUPLICATE_HEADER: 第 {idx[hit]+1} 列与第 {i+1} 列都对应「{hname}」，请合并并明确口径后重新上传。')
            idx[hit] = i
            continue
        e = next((x for x in ext if hname == x['label']), None)
        if e:
            if e['key'] in ext_idx:
                raise ValueError(f'EXCEL_DUPLICATE_HEADER: 扩展字段「{hname}」出现多次，请合并后重新上传。')
            ext_idx[e['key']] = i
        else:
            if hname in unknown:
                raise ValueError(f'EXCEL_DUPLICATE_HEADER: 表头「{hname}」出现多次，请合并后重新上传。')
            unknown[hname] = i
    missing = [c['label'] for c in cols if c['required'] and c['key'] not in idx and c['key'] != 'coverage']
    if missing: raise ValueError('表头缺少本项目字段方案中的必填列：' + '、'.join(missing) + '')
    required = {c['key'] for c in cols if c['required']}
    draft = project['draft']; srcs = {s['id']: s for s in sources}
    tcols = {t['name']: (sid, {c['name'] for c in t['columns']}, t) for sid in draft['source_ids'] for t in srcs.get(sid, {}).get('tables', []) if t['name'] in draft['tables'].get(sid, [])}
    existing = {(t.get('code') or (t.get('config') or {}).get('id')): t for t in draft.get('terms', []) if t.get('code') or (t.get('config') or {}).get('id')}
    names = {t['name']: (t.get('code') or (t.get('config') or {}).get('id')) for t in draft.get('terms', [])}
    dim_values = {d.get('value') for d in draft.get('dimensions', [])}
    from .semantic import validate as vmetric
    from .vendor.plans import validate_plan, PolicyContext
    out, specs, seen = [], [], set()
    data = [(n, r) for n, r in enumerate(rows[1:], 2) if any(v not in (None, '') for v in r)]
    codes_in_file = {str(r[idx['code']] or '').strip() for _, r in data}
    for n, r in data:
        g = lambda k: str(r[idx[k]]).strip() if k in idx and idx[k] < len(r) and r[idx[k]] is not None else ''
        f = {k: g(k) for k, _, _ in COLS}
        cell = lambda i: str(r[i]).strip() if i < len(r) and r[i] is not None else ''
        attrs = {k: cell(i) for k, i in ext_idx.items() if cell(i)}
        attrs.update({'未在方案:' + h: cell(i) for h, i in unknown.items() if cell(i)})
        msgs = []
        rej = lambda code, text: msgs.append({'level': 'reject', 'code': code, 'text': text})
        warn = lambda code, text: msgs.append({'level': 'warn', 'code': code, 'text': text})
        code = f['code']
        if not CODE.match(code): rej('ID_FORMAT', '指标编码须以字母开头，仅含字母、数字、下划线，≤64 位')
        elif code in seen: rej('DUPLICATE', '文件内指标编码重复')
        seen.add(code)
        if not f['name']: rej('NAME_EMPTY', '请填写指标名称')
        if not f['unit'] and 'unit' in required: rej('UNIT_EMPTY', '请填写单位')
        is_group = not f['sql'] and '/' in f['unit'] and any(c != code and c.startswith(code + '_') for c in codes_in_file)
        if not f['definition'] and 'definition' in required: rej('DESC_EMPTY', '业务描述为空')
        if not f['table'] and not is_group:
            warn('TABLE_MISSING', '未填写可查询的物理表；外部已核定统计值也须先确认接入位置、指标编码、数值和期间字段，不能自动猜测明细表')
        parent = f['parent'] or ('_'.join(code.split('_')[:-1]) if code.count('_') >= 2 else '')
        if parent and f['definition']:
            prow = next((pr for _, pr in data if str(pr[idx['code']] or '').strip() == parent), None)
            if prow is not None and str(prow[idx['definition']] or '').strip() == f['definition']: warn('DESC_COPIED', '二级指标照搬了一级业务描述，请写出自身含义')
        sid = None
        if f['source']:
            sid = next((s for s in draft['source_ids'] if srcs.get(s, {}).get('name') == f['source'] or s == f['source']), None)
            if not sid: rej('SOURCE_UNKNOWN', f"数据源「{f['source']}」未接入本项目")
        if f['table'] and f['table'] not in tcols: rej('SEMANTIC_SCHEMA', f"主表 {f['table']} 不在本项目授权表中；导入不会自动扩大授权")
        elif f['table']: sid = sid or tcols[f['table']][0]
        holders = [s for s in draft['source_ids'] if f['table'] in draft['tables'].get(s, [])]
        if not f['source'] and len(holders) > 1:
            rej('SOURCE_AMBIGUOUS', '多个数据源包含同名表，请填写数据源后重试')
        row_cols = {t['name']: (sid, {c['name'] for c in t['columns']}, t) for t in srcs.get(sid, {}).get('tables', []) if t['name'] in draft['tables'].get(sid, [])}
        if f['table'] and sid and f['table'] not in row_cols:
            rej('SEMANTIC_SCHEMA', '主表不在指定数据源的授权范围内')
        structured = None
        if f['metric_config']:
            try:
                structured = json.loads(f['metric_config'])
                if not isinstance(structured,dict) or structured.get('kind')!='semantic-metric' or structured.get('id')!=code:
                    raise ValueError('须为当前指标编码对应的 semantic-metric JSON 对象')
                if structured.get('source_id') not in (sid,'__bind_source__'):
                    raise ValueError('草案数据源与本行授权来源不一致')
                structured['source_id']=sid
                structured['source_revision']=srcs.get(sid,{}).get('target_revision',0)
                from .semantic import Metric
                structured=Metric.model_validate(structured).model_dump()
            except (ValueError,TypeError) as error:
                rej('STRUCTURED_INVALID', '结构化草案格式错误：'+str(error)[:140]); structured=None
            else:
                warn('STRUCTURED_REVIEW', '已接入结构化草案；请核对字段、状态含义、截止期间、缺报/空值和并列记录规则，再在高级配置中确认复核，导入不会代替业务确认')
        for jt in [x.strip() for x in re.split('[；;]', f['join_table']) if x.strip()]:
            if jt not in tcols: rej('SEMANTIC_SCHEMA', f'关联表 {jt} 不在本项目授权表中；导入不会自动扩大授权')
            else:
                t = tcols[jt][2]; keys = [k.split('.')[-1].strip() for k in re.findall(r'关联表\.([\w,]+)', f['join_keys'])]
                if keys and not any(set(u) == set(keys) for u in t.get('unique_keys', []) + ([t['primary_key']] if t.get('primary_key') else [])):
                    if structured and any(o.get('latest') for o in structured.get('operands',[]) if isinstance(o,dict)):
                        msgs.append({'level':'info','code':'LATEST_GRAIN_GUARDED','text':'最新报送结构化计算会在同一查询内检查实体重复、并列最新和缺报，不依赖原始关联键唯一'})
                    elif re.search(r'\b(?:DISTINCT|ROW_NUMBER)\b', f['sql'], re.I):
                        warn('JOIN_GRAIN_REVIEW', f'关联表 {jt} 的原始键非唯一，SQL 中另含去重步骤；需核对去重后的关联粒度及并列记录选择，不能据原表非唯一直接断言查询重复计数')
                    else: warn('FANOUT', f'关联表 {jt} 的关联键缺少数据库唯一约束，可能重复计数；结构化口径需人工确认')
        if f['sql'] and re.search(r'(?i)\bjoin\b', f['sql']) and not (f['join_table'] and f['join_keys']): warn('JOIN_MISSING', 'SQL 示例含 JOIN，但关联表或关联字段为空')
        coverage = None
        if 'coverage' not in idx: warn('COVERAGE_COLUMN', '模板缺少「覆盖期间」列：以文字口径导入，补充期间后才能用于确定性计算')
        elif not f['coverage'] and not is_group: rej('COVERAGE_EMPTY', '覆盖期间为空')
        elif f['coverage']:
            coverage = _periods(f['coverage'])
            if coverage is None: rej('COVERAGE_FORMAT', '覆盖期间格式应为 2023-01~2026-09 或 2023~2025')
        from .vendor.dialect import sqlglot_name
        dialect = sqlglot_name(srcs.get(sid, {}).get('dialect'))
        time_col, formula, filters, notes = parse_caliber(f['caliber'], f['sql'], f['table'], dialect, row_cols)
        if structured:
            time_col=next((o.get('time_column') for o in structured.get('operands',[]) if isinstance(o,dict)),None)
            formula=structured.get('formula',''); filters=''; notes=[]
        try: raw_formula = _caliber_sections(f['caliber']).get('计算公式', '')
        except ValueError: raw_formula = ''
        if not is_group:
            if f['time_format'] and f['time_format'] not in ('date', 'yyyy', 'yyyymm'):
                rej('TIME_FORMAT', '时间字段格式只能填写 date、yyyy 或 yyyymm，须按实际存储格式选择')
            if f['time_format'] == 'yyyy' and re.search(r'月|季度|日', f['time_dims']):
                warn('TIME_GRAIN_CONFLICT', '时间字段只有年份编码，不能登记按月、季度或日查询')
            if not structured and _formula_placeholder(raw_formula):
                warn('FORMULA_UNSPECIFIED', '公式栏是“未提供/待完善”等说明，不是计算公式；如引用外部已核定数值，请明确取值映射，不必编造明细计算')
            elif not formula and not raw_formula and not f['sql'] and not notes:
                rej('FORMULA_MISSING', '统计口径未写明计算公式')
            if not structured:
                for diagnostic in _formula_diagnostics(raw_formula, f['table'], row_cols): warn(*diagnostic)
            if f['sql'] and any('不能完整编译' in note or '需人工复核' in note for note in notes):
                warn('SQL_COMPILE_UNSUPPORTED', 'SQL 示例不能完整转换为当前结构化指标；关联、CTE、取最新记录或与文字口径冲突时保留待复核，不能丢弃条件后生成指标')
            if re.search(r'月|年|季度|日', f['time_dims']) and not time_col:
                warn('TIME_COLUMN_MISSING', '已填写时间粒度，但未能确定实际统计时间字段及格式；年度/月份名称不是物理字段，复杂 SQL 的期间也不会自动推断')
            if coverage and re.fullmatch(r'年(?:度)?', f['time_dims']) and not any(len(p) == 4 for p in coverage):
                warn('COVERAGE_GRANULARITY', '年度口径只填写了部分月份覆盖，尚无完整年度；请确认是完整年度值还是截至某月的快照，不能自动扩成全年')
        years = set(re.findall(r"'(\d{4})-\d{2}-\d{2}'", f['sql']))
        if coverage and years and not years & {p[:4] for p in coverage}: warn('SQL_YEAR_MISMATCH', f"SQL 示例年份 {'、'.join(sorted(years))} 与覆盖期间不一致")
        if not is_group:
            active = {c['key'] for c in cols}
            if 'time_dims' in active and not f['time_dims']: warn('DIM_EMPTY', '时间维度为空')
            regions = [x.strip() for x in re.split('[/、]', f['region_dims']) if x.strip()]
            if 'region_dims' in active and not regions: warn('DIM_EMPTY', '区域维度为空')
            elif dim_values and [x for x in regions if x not in dim_values]: warn('DIM_UNKNOWN', '区域维度不在「维度定义」中：' + '、'.join(x for x in regions if x not in dim_values))
        if f['name'] in names and names[f['name']] not in (code, None): warn('NAME_DUP', f"与已有指标 {names[f['name']]} 同名，问答时可能命中两个口径")
        example_ok = False
        if f['sql'] and sid:
            try:
                validate_plan({'mode': 'sql', 'plan_id': 'xls', 'workspace_id': 'xls', 'datasource_ref': sid, 'logical_sql': f['sql'], 'parameters': [], 'expected_chart': {'type': 'table', 'title': '校验'}, 'limit': 1},
                              PolicyContext(workspace_id='xls', allowed_datasource_refs=[sid], allowed_relations=draft['tables'].get(sid, []), db_types={sid: srcs[sid]['dialect']}))
                example_ok = True
            except Exception as e:
                if 'cannot be parsed as' in str(e):
                    warn('SQL_DIALECT', f'SQL 示例未能按当前数据源 {dialect} 方言解析；请核对目标数据库方言，这不等于原 SQL 在原数据库中无效')
                else: warn('SQL_INVALID', 'SQL 示例未通过只读与授权表校验，示例不导入：' + str(e)[:120])
        spec = None
        if not is_group and not any(m['level'] == 'reject' for m in msgs):
            spec = structured or build_metric(code, {**f, 'time_column': time_col, 'formula': formula, 'filters': filters, 'coverage_list': coverage or []}, sid, row_cols, engine)
            if structured and (set(structured.get('coverage') or [])!=set(coverage or []) or structured.get('unit')!=f['unit']):
                warn('STRUCTURED_CONFLICT','结构化草案的覆盖期间或单位与表格不同，请统一后确认复核')
            if spec and coverage:
                try: vmetric(spec, {**srcs[sid], 'tables': [t for t in srcs[sid]['tables'] if t['name'] in draft['tables'].get(sid, [])]})
                except Exception as e: warn('SEMANTIC_INVALID', '结构化口径未通过校验，以文字口径导入：' + str(e)[:150]); spec = {**spec, '__invalid': True}
            elif not spec:
                reason = next((note for note in notes if '不能完整编译' in note or '需人工复核' in note), '')
                if not reason and filters not in _NO_FILTER and re.search(r'[/÷]', formula):
                    reason = '比率带业务筛选，表格未区分分子和分母；请分别核对两个操作数'
                if not reason and time_col and re.search(r'(?<!\w)'+re.escape(time_col)+r'(?!\w)',filters):
                    reason = '统计时间字段的固定筛选不能自动替换为查询期间'
                # Preserve specific actionable reasons instead of reporting only
                # the same opaque failure for a blank sheet and a complex query.
                detailed = {'TABLE_MISSING','FORMULA_UNSPECIFIED','FORMULA_COLUMN_UNKNOWN','FORMULA_EXPRESSION','FORMULA_MISSING','SQL_COMPILE_UNSUPPORTED','TIME_COLUMN_MISSING'}
                if reason or not any(m['code'] in detailed for m in msgs):
                    warn('PARSE_INCOMPLETE', (reason or '无法完整解析字段、公式或筛选条件')+'，以待复核文字导入')
            for note in notes: msgs.append({'level': 'info', 'code': 'SQL_SAMPLE_NOTE', 'text': note})
        rejected = any(m['level'] == 'reject' for m in msgs)
        row = {'id': (existing.get(code) or {}).get('id') or f'm-{code}', 'name': f['name'], 'definition': f['definition'] or f['name'], 'code': code, 'origin': 'excel', 'caliber_text': f['caliber'][:2000], 'parent_code': parent if parent in codes_in_file or parent in existing else '',
               'time_dims': f['time_dims'], 'region_dims': f['region_dims'], **({'attributes': attrs} if attrs else {})}
        blocking = [m for m in msgs if m['level'] == 'warn' and m['code'] not in ('DIM_EMPTY','SQL_YEAR_MISMATCH')] 
        valid = spec and coverage and not spec.get('__invalid') and not blocking
        if is_group: row['status'] = 'group'
        elif valid: row.update(config=spec, excel_compiler='0.7.5')
        else:
            row['status'] = 'review'; row['review_reason'] = '表格导入的结构化口径不完整，需补全'
            row['excel_input'] = {k:v for k,v in f.items() if v}
            row['review_table'] = f['table']
            if sid: row['source_id'] = sid
            if f['sql']: row['caliber_text'] = (row['caliber_text'] + '\nSQL 示例：' + f['sql']).strip()[:2000]
            if spec and (structured or not f['sql'] or example_ok): row['proposed_config'] = {k: v for k, v in spec.items() if k != '__invalid'}
            elif not spec and filters not in _NO_FILTER and formula and not rejected and (not f['sql'] or example_ok):
                fields = {**f, 'time_column':time_col, 'coverage_list':coverage or []}
                candidate = build_metric(code, {**fields,'formula':formula}, sid,row_cols,engine)
                filtered = build_metric(code, {**fields,'formula':'COUNT(*)','filters':filters},sid,row_cols,engine)
                if candidate and len(candidate['operands']) == 2 and filtered:
                    row.update(proposed_config=candidate, review_filters=filtered['operands'][0]['filters'])
        if blocking and not is_group: row['review_reason'] = '；'.join(m['text'] for m in blocking)[:300]
        old = existing.get(code)
        if old and _protected(old):
            # 0.7.0: metrics from a business pack or with settings a sheet cannot express
            # (YYYYMM time fields, text numbers, period-end snapshots, joins) are never
            # overwritten from Excel; edit them in 指标口径 or re-import the pack.
            out.append({'row': n, 'code': code, 'name': f['name'], 'result': 'unchanged', 'version': '',
                        'messages': [{'level': 'info', 'code': 'PROTECTED', 'text': '该指标来自业务口径包或含表格无法表达的配置，Excel 导入不覆盖；请在「指标口径」中修改。'}]})
            continue
        result = 'rejected' if rejected else 'added' if not old else 'unchanged' if _same(old, row) else 'updated'
        if result != 'rejected' and row.get('status') == 'review': result = 'review' if result != 'unchanged' else 'unchanged'
        version = ''
        if row.get('config'):
            row['config']['version'] = _bump(old, row['config']); version = (f"{(old.get('config') or {}).get('version')} → " if old and old.get('config') else '') + row['config']['version']
        out.append({'row': n, 'code': code, 'name': f['name'], 'result': result, 'needs_review': not rejected and row.get('status') == 'review', 'version': version, 'messages': [m for m in msgs if m['level'] != 'info'] + [m for m in msgs if m['level'] == 'info']})
        if not rejected and result != 'unchanged': specs.append({'term': row, 'example': {'question': f['question'], 'logical_sql': f['sql'], 'source_id': sid, 'metric_code': code} if f['question'] and example_ok and valid and not any(m['code']=='SQL_YEAR_MISMATCH' for m in msgs) else None})
    summary = {k: sum(1 for x in out if x['result'] == k) for k in ('added', 'updated', 'unchanged', 'rejected', 'review')}
    summary['missing'] = len([c for c in existing if c not in codes_in_file])
    receipt = ''
    receipt_rows = [x for x in out if x['result'] == 'rejected' or x.get('needs_review')]
    if receipt_rows:
        rb = op.Workbook(); rs = rb.active; rs.title = '校验回执'
        rs.append(['行号', '结果', '诊断码', '原因'] + header)
        for x in receipt_rows:
            problems = [m for m in x['messages'] if m['level'] in ('reject','warn')]
            rs.append([x['row'], '拒绝' if x['result']=='rejected' else '待完善', '、'.join(m['code'] for m in problems), '；'.join(m['text'] for m in problems)] + ['' if v is None else v for v in rows[x['row'] - 1]])
        buf = io.BytesIO(); rb.save(buf); receipt = base64.b64encode(buf.getvalue()).decode()
    return {'summary': summary, 'rows': out, 'specs': specs, 'receipt_base64': receipt, 'unknown_columns': list(unknown), 'extension_columns': [e['label'] for e in ext if e['key'] in ext_idx]}


def legacy_candidate(row,draft,sources):
    """Rebuild only the editor preview for rows saved before candidates were retained."""
    f=row.get('excel_input') or {}
    if not f or row.get('config') or row.get('proposed_config') or row.get('status')!='review': return {}
    sid=row.get('source_id')
    source=next((x for x in sources if x['id']==sid),None)
    if not source: return {}
    cols={t['name']:(sid,{c['name'] for c in t['columns']},t) for t in source['tables'] if t['name'] in draft['tables'].get(sid,[])}
    from .vendor.dialect import sqlglot_name
    tc,formula,filt,_=parse_caliber(f.get('caliber'),f.get('sql'),f.get('table'),sqlglot_name(source['dialect']),cols)
    fields={**f,'time_column':tc,'coverage_list':_periods(f.get('coverage')) or []}
    candidate=build_metric(row.get('code'),{**fields,'formula':formula},sid,cols,None)
    single=build_metric(row.get('code'),{**fields,'formula':'COUNT(*)','filters':filt},sid,cols,None)
    if candidate and len(candidate['operands'])==2 and single and single['operands'][0]['filters']:
        return {'proposed_config':candidate,'review_filters':single['operands'][0]['filters']}
    return {}


def review_digest(config):
    import hashlib
    return hashlib.sha256(json.dumps(config,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def reviewed(row):
    marker = row.get('reviewed')
    return isinstance(marker,dict) and marker.get('digest') == review_digest(row.get('config'))


def excel_review_reason(row):
    """Read-time quarantine; never rewrite saved or historical project snapshots."""
    if row.get('origin') != 'excel' or reviewed(row): return ''
    if row.get('status') == 'review': return row.get('review_reason') or 'Excel 指标尚待复核'
    cfg = row.get('config') or {}
    if not isinstance(cfg, dict) or cfg.get('kind') != 'semantic-metric' or row.get('excel_compiler') in ('0.7.4','0.7.5'): return ''
    ops = cfg.get('operands', [])
    if not isinstance(ops, list) or any(not isinstance(o,dict) for o in ops): return ''
    if len(ops) == 2 and ops[0].get('filters') and ops[0]['filters'] == ops[1].get('filters'):
        return '旧 Excel 比率对分子和分母使用相同筛选，请分别核实两侧范围'
    if any(o.get('time_column') and any(isinstance(p,dict) and p.get('column') == o['time_column'] for p in o.get('filters') or []) for o in ops):
        return '旧 Excel 口径含固定的统计时间筛选，请核实查询期间'
    if not re.search(r'计算公式\s*[：:]\s*[^\s；;]', row.get('caliber_text') or ''):
        return '旧 Excel 指标的公式来自 SQL 示例，请用原始工作簿重新预览'
    return ''


def _protected(term):
    cfg = term.get('config') or {}
    if reviewed(term) or str(term.get('origin', '')).startswith('pack:'):
        return True
    ops = cfg.get('operands', [])
    return (len(ops) > 1 or (len(ops)==1 and cfg.get('formula')!=ops[0].get('id')) or bool(cfg.get('dimensions') or cfg.get('group_required') or cfg.get('relations'))
            or any(o.get('latest') or o.get('time_format') or o.get('numeric_text') or o.get('aggregation') in ('snapshot_sum','single_value') or o.get('entity_columns')
                   or any(f.get('op') not in ('eq', 'ne') for f in o.get('filters', [])) for o in ops))


def _same(old, new):
    keys = ('name', 'definition', 'caliber_text', 'status', 'source_id', 'parent_code', 'time_dims', 'region_dims')
    if excel_review_reason(old) != excel_review_reason(new): return False
    if old.get('excel_input') != new.get('excel_input'): return False
    if bool(old.get('config')) != bool(new.get('config')): return False
    if (old.get('attributes') or {}) != (new.get('attributes') or {}): return False
    if any((old.get(k) or '') != (new.get(k) or '') for k in keys): return False
    strip = lambda c: {k: v for k, v in (c or {}).items() if k not in ('version', 'source_revision')}
    return strip(old.get('config') or old.get('proposed_config')) == strip(new.get('config') or new.get('proposed_config'))


def _bump(old, cfg):
    oc = (old or {}).get('config')
    if not oc: return '1.0.0'
    a, b, c = (list(map(int, oc.get('version', '1.0.0').split('.'))) + [0, 0, 0])[:3]
    strip = lambda x: {k: v for k, v in x.items() if k not in ('version', 'definition', 'source_revision')}
    return f'{a}.{b + 1}.0' if strip(oc) != strip(cfg) else f'{a}.{b}.{c + 1}'


def apply(draft, result):
    counts = {'added': 0, 'updated': 0}
    terms = draft.setdefault('terms', [])
    for s in result['specs']:
        row = copy.deepcopy(s['term']); i = next((k for k, t in enumerate(terms) if (t.get('code') or (t.get('config') or {}).get('id')) == row['code']), None)
        if i is None: terms.append(row); counts['added'] += 1
        else:
            keep = {k: v for k, v in terms[i].items() if k in ('source',)}
            terms[i] = {**row, **keep, 'id': terms[i]['id']}; counts['updated'] += 1
        e = s.get('example')
        if e and e['source_id']:
            ex = draft.setdefault('examples', [])
            if not any(x.get('metric_code') == e['metric_code'] and x['question'] == e['question'] for x in ex):
                ex.append({'id': 'e-' + row['code'] + '-' + str(len(ex)), **e})
    return draft, counts


def build_metric(code, f, sid, cols, engine):
    """Compile only a fully understood formula and predicate; otherwise retain review text."""
    import sqlglot
    from sqlglot import exp
    table = f.get('table', ''); formula = (f.get('formula') or '').strip()
    if not code or table not in cols or not formula or f.get('join_table') or f.get('join_keys'): return None
    names = cols[table][1]
    def unwrap(node):
        while isinstance(node, exp.Paren): node = node.this
        return node
    def column(node):
        if not isinstance(node, exp.Column) or node.name not in names or node.db or node.catalog or (node.table and node.table != table.split('.')[-1]):
            raise ValueError('unknown column')
        return node.name
    def agg(node):
        node = unwrap(node)
        if isinstance(node, exp.Anonymous) and node.name.upper() == 'SINGLE_VALUE' and len(node.expressions) == 1:
            return 'single_value', column(node.expressions[0])
        kinds = {exp.Count:'count', exp.Sum:'sum', exp.Avg:'avg', exp.Min:'min', exp.Max:'max'}
        if type(node) not in kinds: raise ValueError('unsupported aggregate')
        if node.expressions: raise ValueError('unsupported aggregate arguments')
        arg = node.this
        if isinstance(arg, exp.Distinct):
            if not isinstance(node, exp.Count) or len(arg.expressions) != 1: raise ValueError('unsupported distinct')
            return 'distinct', column(arg.expressions[0])
        if isinstance(node, exp.Count) and isinstance(arg, exp.Star) and not any(arg.args.values()): return 'count', None
        return kinds[type(node)], column(arg)
    def literal(node):
        if isinstance(node, exp.Literal):
            return node.this if node.is_string else float(node.this) if '.' in node.this or 'e' in node.this.lower() else int(node.this)
        if isinstance(node, exp.Boolean): return node.this
        if isinstance(node, exp.Neg):
            value = literal(node.this)
            if type(value) in (int, float): return -value
        raise ValueError('unsupported filter value')
    def predicate(node):
        node = unwrap(node)
        if isinstance(node, exp.And): return predicate(node.this) + predicate(node.expression)
        if isinstance(node, exp.In) and not node.args.get('query'):
            return [{'column':column(node.this), 'op':'in', 'values':[literal(x) for x in node.expressions]}]
        kinds = {exp.EQ:'eq', exp.NEQ:'ne', exp.GT:'gt', exp.GTE:'gte', exp.LT:'lt', exp.LTE:'lte'}
        if type(node) not in kinds: raise ValueError('unsupported predicate')
        return [{'column':column(node.this), 'op':kinds[type(node)], 'values':[literal(node.expression)]}]
    try:
        parsed = sqlglot.parse(_sheet_expr(formula))
        if len(parsed) != 1: return None
        expr = unwrap(parsed[0]); scale = False
        if isinstance(expr, exp.Mul) and isinstance(expr.expression, exp.Literal) and not expr.expression.is_string and expr.expression.this == '100':
            scale = True; expr = unwrap(expr.this)
        ops = [agg(expr.this), agg(expr.expression)] if isinstance(expr, exp.Div) else [agg(expr)]
        if any(g == 'single_value' for g, _ in ops) and len(ops) != 1: return None
        if scale and len(ops) != 2: return None
        raw_filter = (f.get('filters') or '').strip()
        if raw_filter in _NO_FILTER: filters = []
        else:
            if len(ops) == 2: return None  # The sheet doesn't assign predicates to operands.
            trees = sqlglot.parse(_sheet_expr(raw_filter, filters=True))
            if len(trees) != 1: return None
            filters = predicate(trees[0])
    except (ValueError, TypeError, sqlglot.errors.SqlglotError):
        return None
    time_col = f.get('time_column') or None
    if time_col and (time_col not in names or any(p['column'] == time_col for p in filters)): return None
    if time_col and '全量' in (f.get('time_dims') or ''): return None
    if not time_col and re.search(r'月|年|季度|日', f.get('time_dims') or ''): return None
    ids = ['value'] if len(ops) == 1 else ['numerator', 'denominator']
    operands = [{'id': i, 'table': table, 'column': c, 'aggregation': g, 'time_column': time_col, 'filters': copy.deepcopy(filters), 'entity_columns': []} for i, (g, c) in zip(ids, ops)]
    coverage = f.get('coverage_list') or []
    time_format = f.get('time_format') or None
    if time_format:
        if time_format not in ('date','yyyy','yyyymm') or not time_col: return None
        for operand in operands: operand['time_format'] = time_format
        if time_format == 'yyyy': coverage = [p for p in coverage if len(p) == 4]
    return {'kind': 'semantic-metric', 'id': code, 'version': '1.0.0', 'source_id': sid, 'unit': (f.get('unit') or '项')[:30], 'definition': (f.get('definition') or f.get('name') or code)[:3000],
            'time_mode': 'period' if time_col else 'all', 'coverage': coverage if time_col else [], 'operands': operands if time_col else [{**o, 'time_column': None} for o in operands],
            'formula': 'value' if len(ops) == 1 else 'numerator / denominator' + (' * 100' if scale else ''), 'dimensions': [], 'relations': [], 'additive': len(ops) == 1 and ops[0][0] in ('count', 'sum')}


class ExcelImports:
    """配置管理里的「Excel 口径导入」：下载模板 → 上传预览（逐行校验、错误回执）→ 确认写入草稿；记录导入历史。"""
    def __init__(self, engine, business):
        self.engine, self.business = engine, business
        self.jobs, self.lock = {}, threading.Lock()
        self.history_path = Path(engine.home) / 'excel-history.json'

    def _project(self, actor, bid, role):
        return self.business.require(self.business.config(), actor, bid, role)

    def template(self, actor, a):
        project = self._project(actor, a.get('business_project'), 'editor')
        import base64
        data = template(project, self.engine.config()['sources'], bool(a.get('prefill')))
        return {'name': f"业务口径{'_' + project['name'] if a.get('prefill') else '导入模板'}.xlsx", 'content_base64': base64.b64encode(data).decode()}

    def import_(self, actor, a):
        import base64
        bid = a.get('business_project'); baseline = self.business.config()
        project = self.business.require(baseline, actor, bid, 'editor')
        if a.get('mode') == 'preview':
            raw = base64.b64decode(a.get('content_base64') or '')
            if not raw or len(raw) > 5 * 1048576: raise ValueError('文件为空或超过 5MB')
            name = str(a.get('name') or '业务口径.xlsx')[:200]
            if not name.lower().endswith('.xlsx'): raise ValueError('请上传 .xlsx 格式的业务口径表格')
            result = parse(raw, project, self.engine.config()['sources'], self.engine)
            token = uuid.uuid4().hex
            with self.lock:
                now = time.time()
                self.jobs = {k: v for k, v in self.jobs.items() if now - v['at'] < 1800}
                self.jobs[token] = {'actor': actor, 'project': bid, 'revision': baseline['revision'], 'result': result, 'name': name, 'at': now}
            return {k: v for k, v in result.items() if k != 'specs'} | {'token': token}
        with self.lock:
            token = a.get('token') or ''
            job = self.jobs.get(token)
            if job and job['project'] == bid and job['actor'] == actor: self.jobs.pop(token)
        if not job or job['project'] != bid or job['actor'] != actor or time.time() - job['at'] > 1800:
            raise ValueError('导入预览已失效，请重新上传')
        cfg = self.business.config(); project = self.business.require(cfg, actor, bid, 'editor')
        if cfg['revision'] != job['revision'] or a.get('expected_revision', job['revision']) != job['revision']:
            from natural_work.store import ConflictError
            raise ConflictError('项目配置自预览后已变化，请重新预览 Excel 后再导入。')
        draft, counts = apply(copy.deepcopy(project['draft']), job['result'])
        value = self.business.update(actor, 'draft', {'business_project': bid, 'draft': draft, 'expected_revision': job['revision']})
        with self.lock:
            hist = json.loads(self.history_path.read_text()) if self.history_path.exists() else {'items': []}
            hist['items'] = (hist['items'] + [{'at': time.time(), 'project': bid, 'name': job['name'], 'actor': actor, **job['result']['summary']}])[-500:]
            tmp = self.history_path.with_suffix('.tmp'); tmp.write_text(json.dumps(hist, ensure_ascii=False, indent=1)); tmp.replace(self.history_path)
        return {**value, 'import_counts': counts}

    def history(self, actor, a):
        bid = a.get('business_project'); self._project(actor, bid, 'viewer')
        items = json.loads(self.history_path.read_text())['items'] if self.history_path.exists() else []
        return {'items': list(reversed([x for x in items if x['project'] == bid]))}
