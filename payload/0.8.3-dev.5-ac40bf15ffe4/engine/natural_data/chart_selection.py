"""Deterministic chart switching: advertise only types we can actually build."""
import math
import re
from .vendor.charts import _field, recommend_chart, _hard_blocked, _column_kind, _extract_explicit_chart, SERIES_READABILITY_LIMIT
from .vendor.metric_semantics import is_additive
from .vendor.pivot import MAX_COLUMN_CARDINALITY, MAX_ROW_CARDINALITY

CHART_KINDS=('table','kpi','bar','column','grouped-bar','stacked-bar','percent-stacked-bar','line','area','pie','donut','scatter','bubble','heatmap','treemap','histogram','radar','funnel','gauge','waterfall','boxplot','alert-table','pivot-table')
_STACKS = {'stacked-bar', 'percent-stacked-bar'}
_TRENDS = {'line', 'area'}
_MATRIX_CHARTS = _STACKS | _TRENDS | {'grouped-bar', 'heatmap'}
_SHARED_VALUE_SCALE = _STACKS | _TRENDS | {'radar'}
# Reuse the product's cross-tab layout budget for renderers that materialize
# every category/series/measure slot. Complete data needs no padding budget.
_MAX_PADDED_MATRIX_CELLS = MAX_ROW_CARDINALITY * MAX_COLUMN_CARDINALITY

def heterogeneous_units(result):
    unit = str((result.get('metric_contract') or {}).get('unit', '')).strip().lower()
    if unit in ('见单位', '见指标单位', '各指标单位', '混合', 'mixed', 'varies'):
        return True
    labels = result.get('column_labels') or {}
    for column in result.get('columns') or []:
        if str(labels.get(column, column)).strip().lower() in ('单位', '计量单位', 'unit', 'units'):
            if len({str(r.get(column)).strip() for r in result.get('rows') or [] if r.get(column) not in (None, '')}) > 1:
                return True
    return False

def chart_for(result, kind):
    """Return an eligible spec, including proof that its renderer has data."""
    shape = _shape(result)
    return _eligible_chart(result, kind, shape, {})


def chart_spec_renderable(result, spec):
    """Validate a saved encoding against current rows without changing it."""
    kind = spec.get('type')
    shape = _shape(result)
    return kind in CHART_KINDS and _suitable(result, kind, shape) and _renderable(result, kind, spec, shape)


def _eligible_chart(result, kind, shape, matrices):
    if kind not in CHART_KINDS or not _suitable(result, kind, shape):
        return None
    spec = _build_chart(result, kind, shape)
    if spec:
        x=(spec.get('encoding') or {}).get('x') or {}
        if grain := shape['period_fields'].get(x.get('field')):
            x['grain'] = grain
    return spec if spec and _renderable(result, kind, spec, shape, matrices) else None


def _build_chart(result, kind, shape):
    if heterogeneous_units(result):
        return {'type':'table','title':result.get('title',''),'encoding':{},'allowedAlternatives':['table'],
                'disclosure':{'topN':None,'sampled':False,'note':'计量单位不同或未统一，按指标分别展示数值，不共用数值轴。'}} if kind=='table' else None
    if result.get('metric_contract') and 'value' in result.get('columns',[]):
        # A numeric grouping key remains a dimension. The legacy recommender
        # otherwise invents a second measure/series (e.g. a completion value
        # grouped alongside the project name and repeated as the metric).
        # Keep the full table until a supported encoding can preserve that
        # dimension; never silently drop it or plot it as an extra metric.
        dimensions = (result['metric_contract'].get('scope') or {}).get('dimensions') or []
        if kind not in ('table','alert-table','pivot-table') and any(
            d in result.get('columns', []) and _column_kind(d,[r.get(d) for r in result.get('rows',[])])=='numeric'
            for d in dimensions):
            return None
        # Use declared metric semantics, not an ambiguous SQL alias, for compatibility.
        mapped=dict(result);contract=mapped.pop('metric_contract')
        if kind=='gauge' and contract['unit']!='%':return None
        alias='metric_rate' if contract['unit']=='%' else 'metric_amount'
        rename={'value':alias,'period':'stat_year' if all(len(str(r.get('period','')))==4 for r in mapped['rows']) else 'month'}
        visible=[c for c in mapped['columns'] if c not in ['cumulative','total']]
        if mapped['rows'] and 'period' not in shape['period_fields'] and len({str(r.get('period')) for r in mapped['rows']})==1:
            visible=[c for c in visible if c!='period']
        mapped['columns']=[rename.get(c,c) for c in visible]
        mapped['rows']=[{rename.get(c,c):row[c] for c in visible} for row in mapped['rows']]
        mapped['column_labels']={rename.get(k,k):v for k,v in mapped.get('column_labels',{}).items()}
        if shape['period_fields']:
            period_proof = (mapped.get('validation') or {}).get('period_grain') or {}
            mapped['validation']={**mapped['validation'],'period_grain':{**period_proof,'columns':[
                {**field,'field':rename.get(field['field'],field['field'])} for field in period_proof['columns']]}}
        spec=_build_chart(mapped,kind,_shape(mapped))
        inverse={v:k for k,v in rename.items()}
        def restore(value):
            if isinstance(value,str):return inverse.get(value,value)
            if isinstance(value,list):return [restore(x) for x in value]
            if isinstance(value,dict):return {inverse.get(k,k):restore(v) for k,v in value.items()}
            return value
        spec = restore(spec)
        if spec:
            measures = spec.get('encoding',{}).get('y', [])
            for measure in measures if isinstance(measures,list) else [measures]:
                if measure.get('field') == 'value': measure['unit'] = contract['unit']
        return spec
    columns,rows=result.get('columns',[]),result.get('rows',[])
    if kind not in ('table','alert-table','pivot-table'):
        rates=[c for c in columns if re.search(r'(?:^|_)(?:rate|ratio|percent|pct)(?:$|_)|率|占比|百分比',c,re.I)]
        auxiliaries=[c for c in columns if re.fullmatch(r'(?:total_)?(?:batches|samples?|sample_count|record_count|count|denominator|numerator)',c,re.I)]
        if rates and auxiliaries:
            selected={**result,'columns':[c for c in columns if c not in auxiliaries]}
            spec=_build_chart(selected,kind,_shape(selected))
            if spec:spec['note']='图表展示比率指标；样本数等计算依据保留在数据页。'
            return spec
    numeric=shape['measures'];dims=shape['dims'];dates=shape['dates']
    if kind=='table':
        # F4：默认降级表格（系列数超限等）带有披露说明与透视表候选；重建表格
        # spec 时保留它们，否则 decorate_chart 会把降级原因抹掉。
        current=result.get('chart') or {}
        spec={'type':'table','encoding':{}}
        if current.get('type')=='table':
            for key in ('disclosure','pivot','allowedAlternatives'):
                if current.get(key):spec[key]=current[key]
        return spec
    if not rows:return None
    if kind in ('bar','column') and shape['rows']==1 and numeric:
        axes = dates+dims
        encoding={'y':[_field(c,(result.get('column_labels') or {}).get(c)) for c in numeric],
                  **({'x':_field(axes[0],(result.get('column_labels') or {}).get(axes[0]))} if len(axes)==1 else {})}
        return {'type':kind,'title':result.get('title',''),'encoding':encoding,'layout':'scalar-bars'}
    if kind in ('bar','column') and numeric and len(dates+dims)==1 and (
            shape['period_fields'] or len(numeric)>1):
        axis=(dates+dims)[0]
        labels=result.get('column_labels') or {}
        return {'type':kind,'title':result.get('title',''),
                **({'layout':'measure-panels'} if len(numeric)>1 else {}),
                'encoding':{'x':_field(axis,labels.get(axis)),'y':[_field(c,labels.get(c)) for c in numeric]}}
    if kind in _TRENDS and len(dates)==1 and not dims and dates[0] in shape['period_fields']:
        labels=result.get('column_labels') or {}
        return {'type':kind,'encoding':{'x':_field(dates[0],labels.get(dates[0])),
                                        'y':[_field(c,labels.get(c)) for c in numeric]}}
    if kind in ('pie','donut') and _complete_category_whole(result,numeric,dims,dates):
        labels=result.get('column_labels') or {}
        return {'type':kind,'encoding':{'x':_field(dims[0],labels.get(dims[0])),
                                        'y':_field(numeric[0],labels.get(numeric[0]))}}
    if kind in ('gauge','kpi') and len(rows)!=1:return None
    # Long-form category × period is a real series, not a reason to fall back
    # to a table. Handle it before the legacy recommender's narrower shapes.
    if len(numeric)==1 and len(dims)+len(dates)==2 and kind in _MATRIX_CHARTS:
        x, series = dates+dims
        labels=result.get('column_labels',{})
        return {'type':kind,'encoding':{'x':{'field':x,'label':labels.get(x,x)},'series':{'field':series,'label':labels.get(series,series)},'y':[{'field':numeric[0],'label':labels.get(numeric[0],numeric[0])}]}}
    if _hard_blocked(kind,numeric,dims,dates,rows):return None
    # A toggle changes presentation, never silently discards measures or invents
    # dimensions. Wide heterogeneous values cannot truthfully be stacked.
    if kind in ('pie','donut','treemap','funnel','waterfall','boxplot','histogram','bar','column') and len(numeric)!=1:return None
    if kind in _STACKS:return None
    if kind in ('pie','donut','treemap') and not _complete_category_whole(result,numeric,dims,dates):return None
    if kind=='alert-table':
        rules=result.get('alert_rules') or result.get('chart',{}).get('alertRules')
        return {'type':kind,'encoding':{},'alertRules':rules} if rules else None
    spec=recommend_chart(columns,rows,expected_type=kind,explicit_type=kind,title=result.get('title',''),labels=result.get('column_labels'))
    if spec.get('type')!=kind:return None
    if kind in ('bar', 'column'):
        # A unique time/category axis can plot each row once. The table
        # retains any auxiliary dimension the simple bars do not display.
        spec['encoding'].pop('series', None)
    pivot=spec.get('pivot')
    if pivot and any(not is_additive(c) for c in pivot.get('values',[])):
        for total in (pivot.get('totals') or {}).values():total.update(showGrandTotals=False,showSubTotals=False)
        for c,agg in pivot.get('aggregations',{}).items():
            if not is_additive(c) and agg=='sum':pivot['aggregations'][c]='avg'
        pivot['note']='不可加总指标已隐藏总计；单元格均值为简单平均，非总体加权值。'
    return spec



# ---- 0.6.2: one rule set for the default chart and the chart menu ---------------
# "Buildable" is not enough: a rate split into a funnel or a waterfall, or a box plot
# of two aggregated numbers, can be drawn but means nothing. Every offered type must
# also suit the data, and the default is the first suitable type in a fixed order,
# so a chart that cannot be built falls back to the next good chart, not a table.
_STAGE_HINT = re.compile(r'阶段|环节|流程|转化|漏斗|步骤|节点')
_CHANGE_HINT = re.compile(r'增减|变化|变动|构成|贡献|瀑布|拆解')
_RATE_NAME = re.compile(r'(?:^|_)(?:rate|ratio|percent|pct)(?:$|_)|率|占比|百分比|比例', re.I)
# Chinese counts the shared additivity word list does not know (件数, 办结量, 次数 ...).
_CN_COUNT = re.compile(r'增减|变化量|净增|净变化|差额|件数|次数|人数|户数|家数|本数|张数|批数|单位数|证书数|项数|办结量|办理量|受理量|审批量|审核量|发证量|(?<![均存])量$|(?<![指系分倍均])数$')
_NOT_ADDITIVE = re.compile(r'库存|存量|结存|余额|均|率|占比|比例|指数')
_SUMMARY_CATEGORY = re.compile(r'^(?:合计|总计|小计|汇总|grand\s+total|sub\s*total|total)$', re.I)


def _additive_name(column):
    name = str(column)
    return is_additive(name) or (bool(_CN_COUNT.search(name)) and not _NOT_ADDITIVE.search(name))


def _complete_category_whole(result, measures, categories, dates):
    if result.get('truncated') or len(measures)!=1 or len(categories)!=1 or dates:
        return False
    rows=result.get('rows') or []
    if not rows:
        return False
    measure, category = measures[0], categories[0]
    labels=result.get('column_labels') or {}
    measure_label=str(labels.get(measure, ''))
    contract=result.get('metric_contract') or {}
    if contract and measure == 'value':
        if contract.get('unit') in ('%', '百分点') or contract.get('ratio') or contract.get('additive') is False:
            return False
    else:
        if not (_additive_name(measure) or _additive_name(measure_label)) or _RATE_NAME.search(measure+' '+measure_label):
            return False
        # The SQL contract is generated from the executed statement; unique
        # category labels alone cannot prove separately counted entities disjoint.
        if (result.get('sql_answer_contract') or {}).get('grouped_distinct') is True:
            return False
    values=[row.get(measure) for row in rows]
    if not all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in values) or not any(v>0 for v in values):
        return False
    names=[str(row.get(category) or '').strip() for row in rows]
    return len(names)==len(set(names)) and all(n and not _SUMMARY_CATEGORY.fullmatch(n) for n in names)


def _shape(result):
    columns = list(result.get('columns') or [])
    rows = list(result.get('rows') or [])
    contract = result.get('metric_contract') or {}
    period_proof = (result.get('validation') or {}).get('period_grain') or {}
    period_fields = {c['field']:c['grain'] for c in period_proof.get('columns', [])
                     if period_proof.get('state')=='checked' and c['field'] in columns and not c.get('part')}
    if contract and 'value' in columns:
        columns = [c for c in columns if c not in ('cumulative', 'total')]
        if rows and 'period' in columns and 'period' not in period_fields and len({str(r.get('period')) for r in rows}) == 1:
            columns = [c for c in columns if c != 'period']
    kinds = {c: 'datetime' if c in period_fields else _column_kind(c, [r.get(c) for r in rows]) for c in columns}
    measures = [c for c in columns if kinds[c] == 'numeric']
    dims = [c for c in columns if kinds[c] == 'categorical']
    dates = [c for c in columns if kinds[c] == 'datetime']
    if contract and 'value' in columns:
        rate = contract.get('unit') == '%' or bool(contract.get('ratio'))
        # Pie/treemap need additivity across categories (甲级 + 乙级), not over time;
        # a snapshot count is not additive over months but is across grades.
        additive = not rate and contract.get('additive') is not False
    else:
        labels = result.get('column_labels') or {}
        rate = any(_RATE_NAME.search(str(c) + str(labels.get(c, ''))) for c in measures)
        additive = bool(measures) and not rate and all(_additive_name(c) or _additive_name(labels.get(c, '')) for c in measures)
    values = [r.get(c) for r in rows for c in measures]
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    labels_text = ' '.join(str((result.get('column_labels') or {}).get(c, c)) for c in columns)
    names = {c: str(c) + str((result.get('column_labels') or {}).get(c, '')) for c in measures}
    rate_flags = {bool(_RATE_NAME.search(names[c])) for c in measures}
    return {'rows': len(rows), 'measures': measures, 'dims': dims, 'dates': dates, 'period_fields': period_fields,
            'rate': rate, 'additive': additive,
            'nulls': any(v is None for v in values), 'negatives': any(v < 0 for v in numbers),
            'categories': len({tuple(str(r.get(c)) for c in dims + dates) for r in rows}),
            'points': len({str(r.get(dates[0])) for r in rows}) if dates else 0,
            # A rate next to amounts (执行率 with 预算/支出) cannot share one bar scale.
            'mixed_units': len(rate_flags) > 1,
            'text': (result.get('question') or '') + ' ' + (result.get('title') or '') + ' ' + labels_text}


def _suitable(result, kind, s):
    """Product-level shape policies, before checking the generated encoding."""
    if heterogeneous_units(result):return kind=='table'
    one_measure = len(s['measures']) == 1
    whole = s['additive'] and not s['nulls'] and not s['negatives']
    if kind in ('table', 'alert-table', 'pivot-table'):
        return True
    # An indicator ID is not a readable category. Keep the exact codes in
    # the table; a query projecting the business name can draw a labeled chart.
    if any(c.lower() in ('zbx_id','indicator_code') for c in s['dims']):
        return False
    if kind == 'kpi':
        return s['rows'] == 1
    if kind == 'gauge':
        return s['rows'] == 1 and s['rate']
    if kind in ('line', 'area'):
        return s['points'] >= 2  # offered from 2 points; chosen by default only from 3
    if kind in ('bar', 'column'):
        return s['rows'] >= 2 or (s['rows']==1 and len(s['dims'])+len(s['dates'])<=1
                                 and 0<len(s['measures'])<=4)
    if kind in ('pie', 'donut'):
        return _complete_category_whole(result,s['measures'],s['dims'],s['dates'])
    if kind == 'treemap':
        return one_measure and s['categories'] >= 3 and _complete_category_whole(result,s['measures'],s['dims'],s['dates'])
    if kind == 'funnel':
        return one_measure and whole and s['rows'] >= 3 and bool(_STAGE_HINT.search(s['text']))
    if kind == 'waterfall':
        return one_measure and s['additive'] and not s['nulls'] and s['rows'] >= 3 and (s['negatives'] or bool(_CHANGE_HINT.search(s['text'])))
    if kind in ('boxplot', 'histogram'):
        return s['rows'] >= 30  # a distribution needs detail rows, not a handful of totals
    if kind in ('scatter', 'bubble'):
        return len(s['measures']) >= 2 and s['rows'] >= 10
    if kind == 'radar':
        return 3 <= s['categories'] <= 12 and not s['mixed_units']
    if kind in _STACKS:
        grouped_distinct = (result.get('sql_answer_contract') or {}).get('grouped_distinct')
        return (one_measure and s['additive'] and not s['negatives'] and not s['mixed_units']
                and not grouped_distinct and not result.get('truncated'))
    if kind == 'grouped-bar':
        return not s['negatives'] and not s['mixed_units']
    return True


def _number(value):
    # ProductVisualization accepts finite numbers and numeric strings. A
    # formatted string such as "1,000" may pass legacy column inference while
    # the renderer cannot turn it into a point.
    if type(value) not in (int, float, str):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _dimension(value):
    # Match the renderer's String(value ?? '') and its composite cell key.
    if value is None:
        return ''
    if isinstance(value, bool):
        return str(value).lower()
    if type(value) is float and value.is_integer():
        return str(int(value))
    return str(value)


def _matrix_types(rows, x, series, y):
    cells = set()
    axes = set()
    series_values = set()
    observed = set()
    positive_axes = set()
    valid_stacks = bool(series)
    for row in rows:
        axis = _dimension(row.get(x))
        label = _dimension(row.get(series)) if series else ''
        if '\x01' in axis or '\x01' in label or (axis, label) in cells:
            return set()
        cells.add((axis, label))
        axes.add(axis)
        series_values.add(label)
        values = [_number(row.get(field)) for field in y]
        if any(value is not None for value in values):
            observed.add(label)
        if valid_stacks:
            if not axis or not label or any(value is None or value < 0 for value in values):
                valid_stacks = False
            elif any(value > 0 for value in values):
                positive_axes.add(axis)
    grid_size = len(axes) * len(series_values)
    if series and grid_size > len(cells) and grid_size * len(y) > _MAX_PADDED_MATRIX_CELLS:
        return set()
    types = set()
    if observed and observed == series_values:
        types.update(_TRENDS | {'grouped-bar'})
    if observed and len(axes) >= 2 and len(series_values) >= 2:
        types.add('heatmap')
        # Unique cells plus exact cardinality prove completeness in one pass.
        # Each incomplete stack would be entirely blank in GroupedBarView.
        if valid_stacks and len(cells) == grid_size:
            types.add('stacked-bar')
            if positive_axes == axes:
                types.add('percent-stacked-bar')
    return types


def _different_units(result, measures):
    if len(measures) < 2:
        return False
    from .display_units import _COLUMN_UNIT

    units = set()
    contract = result.get('metric_contract') or {}
    evidence = result.get('column_unit_evidence') or {}
    sql_units = result.get('sql_column_units') or {}
    labels = result.get('column_labels') or {}
    saved = (result.get('chart') or {}).get('encoding') or {}
    saved_measures = saved.get('y') or []
    if isinstance(saved_measures, dict):
        saved_measures = [saved_measures]
    saved_by_field = {measure.get('field'): measure for measure in saved_measures}
    for measure in measures:
        field = measure['field']
        entry = evidence.get(field) or {}
        unit = (contract.get('unit') if field == 'value' else None) or entry.get('unit') or sql_units.get(field)
        if not unit:
            label = labels.get(field) or measure.get('label') or field
            explicit = _COLUMN_UNIT.search(label) or _COLUMN_UNIT.search(field)
            if explicit:
                unit = explicit.group(1)
            else:
                for descriptor in (measure, saved_by_field.get(field) or {}):
                    caption = descriptor.get('label') or label
                    declared = descriptor.get('unit')
                    if declared and declared != _field(field, caption).get('unit'):
                        unit = declared
                        break
        if unit:
            units.add(str(unit).strip().replace('％', '%'))
    return len(units) > 1


def _renderable(result, kind, spec, shape, matrices=None):
    if kind == 'table':
        return True
    if kind == 'alert-table':
        return bool(spec.get('alertRules'))
    if kind == 'pivot-table':
        return bool(spec.get('pivot', {}).get('values'))

    rows = result.get('rows') or []
    columns = set(result.get('columns') or [])
    encoding = spec.get('encoding') or {}
    x = (encoding.get('x') or {}).get('field')
    series = (encoding.get('series') or {}).get('field')
    measures = encoding.get('y') or []
    if isinstance(measures, dict):
        measures = [measures]
    y = [field.get('field') for field in measures]

    if kind == 'histogram':
        return x in columns and sum(_number(row.get(x)) is not None for row in rows) >= 2
    if not rows or not y or any(field not in columns for field in y):
        return False
    if kind in _SHARED_VALUE_SCALE and _different_units(result, measures):
        return False
    if kind in ('kpi', 'gauge'):
        numbers = [_number(rows[0].get(field)) is not None for field in y]
        return any(numbers) if kind == 'kpi' else all(numbers)
    if spec.get('layout')=='scalar-bars':
        return (kind in ('bar','column') and len(rows)==1 and (not x or x in columns and x not in y)
                and set(y)==set(shape['measures']) and (not x or len(shape['dims'])+len(shape['dates'])==1)
                and any(_number(rows[0].get(field)) is not None for field in y))
    if x not in columns:
        return False
    if kind in ('scatter', 'bubble'):
        required = [x, y[0]]
        if kind == 'bubble':
            size = (encoding.get('size') or {}).get('field')
            if size not in columns:
                return False
            required.append(size)
        return any(all(_number(row.get(field)) is not None for field in required) for row in rows)
    if kind == 'boxplot':
        return any(_number(row.get(y[0])) is not None for row in rows)
    if kind in _MATRIX_CHARTS:
        if series and (series not in columns or series == x):
            return False
        if kind in _STACKS and (not series or len(y) != 1):
            return False
        if kind == 'heatmap' and not series:
            return False
        if set(shape['dims'] + shape['dates']) - {x, series}:
            return False
        if len(y) != len(set(y)) or any(field in (x, series) for field in y):
            return False
        matrices = {} if matrices is None else matrices
        coordinates = (x, series, tuple(y))
        if coordinates not in matrices:
            matrices[coordinates] = _matrix_types(rows, x, series, y)
        return kind in matrices[coordinates]

    # Single-axis views cannot explain repeated categories or show a second
    # series. A dimension paired one-to-one with the horizontal axis remains
    # available in the data table without collapsing separate values in a plot.
    if series:
        return False
    if len({_dimension(row.get(x)) for row in rows}) != len(rows):
        return False
    if kind in ('pie', 'donut', 'treemap', 'funnel'):
        return any((value := _number(row.get(y[0]))) is not None and value > 0 for row in rows)
    if kind in ('radar', 'waterfall'):
        # These views would turn unknown observations into zero.
        return all(_number(row.get(field)) is not None for row in rows for field in y)
    return any(_number(row.get(field)) is not None for row in rows for field in y)


def suitable(result, kind):
    """Report the same eligibility used by switching, menus and history."""
    return chart_for(result, kind) is not None


def _series_overloaded(result, s):
    """F4：单度量 + 双轴（期间×类别 或 两分类维）时，第二轴是系列。

    系列数超过 SERIES_READABILITY_LIMIT 时，默认图形不应再选折线/分组柱等
    多系列图——与 vendor.charts 的 P 类守卫同一口径。只影响默认选择；
    suitable/available_types 仍提供这些图形供用户显式切换。
    """
    if len(s['measures']) != 1:
        return False
    axes = s['dates'] + s['dims']
    if len(axes) != 2:
        return False
    series = axes[1]
    rows = result.get('rows') or []
    return len({str(r.get(series)) for r in rows}) > SERIES_READABILITY_LIMIT


def default_type(result, *, honor_explicit=True, available=None):
    """The first suitable, buildable type in a fixed order of preference."""
    # F4：显式用户图形优先（L1）。question 里点名的图表与 recommend_chart 同一
    # 词表判定，只要可建且适配就直接返回，不被下面的形态/系列数默认规则否决。
    explicit = _extract_explicit_chart(result.get('question') or '') if honor_explicit else None
    if explicit == 'table':
        return 'table'
    s = _shape(result)
    matrices = {}
    if explicit and _eligible_chart(result, explicit, s, matrices):
        return explicit
    # Multi-attribute records need a row-by-row view by default. Repeated
    # category combinations can be real details, not missing chart cells.
    question = result.get('question') or ''
    enumeration = bool(s['dates'] and s['dims'] and re.search(r'(?:各|逐|每)(?:月|周|日|季|年)[、，,和与及\s]*(?:各|每|按)',question)
                       and not re.search(r'趋势|走势|变化|同比|环比',question))
    if s['rows'] > 1 and (enumeration or re.search(r'明细|名单|清单|逐条|记录列表|\bdetail\b|\brecords\b',
                                  question, re.I)
                          or len(s['dims']) >= 2 and (len(s['measures']) > 1
                                                      or s['categories'] < s['rows'])):
        return 'table'
    order = ['kpi'] if s['rows'] == 1 else []
    if re.search(r'占比|构成|比重|结构|份额', s['text']):
        order += ['pie', 'donut']  # a share question reads best as parts of a whole
    if s['points'] >= 3:
        order += ['line']
    if len(s['measures']) == 1 and len(s['dims']) + len(s['dates']) == 1:
        order += ['column', 'bar'] if s['categories'] <= 8 else ['bar', 'column']
    order += ['grouped-bar', 'heatmap', 'line', 'bar', 'table']
    overloaded = _series_overloaded(result, s)
    for kind in order:
        if kind in ('pie','donut') and not 2 <= s['categories'] <= 8:
            continue
        if kind in ('bar','column') and len(s['measures']) > 1:
            continue  # Independent panels are available on demand.
        if overloaded and kind not in ('table', 'alert-table', 'pivot-table'):
            # F4：单度量双轴时第二轴必为系列，任何图形（含 bar）都会画出超量
            # 系列，默认一律落到表格；显式切换仍可用这些图形。
            continue
        if (kind in available if available is not None else _eligible_chart(result, kind, s, matrices)):
            return kind
    return 'table'


def available_types(result):
    shape = _shape(result)
    matrices = {}
    return [kind for kind in CHART_KINDS if _eligible_chart(result, kind, shape, matrices) is not None]
