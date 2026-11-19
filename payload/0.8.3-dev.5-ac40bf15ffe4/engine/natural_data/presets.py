"""业务口径包：随插件内置、按实际库匹配导入草稿的业务配置。

口径包沿用业务模板格式（ask-data-business-template v2），另加 `pack` 元信息。插件不认识任何具体业务：
不同业务各做一个包，放在 presets/ 目录。导入时：

- 按表名、字段名（不区分大小写，兼容 schema 前缀）对照项目已授权的目录；对不上的条目跳过并列出原因，不整包失败；
- 结构化指标的覆盖期间按库里该时间字段的实际最早、最晚值推算（只读聚合查询），发布前由管理员核对；
- 查询示例可按方言提供多个写法（variants），按数据源方言选取，并把表名换成库里的实际名称；
- 条目带来源标记，重复导入只替换本包条目；管理员改过的术语和示例保留不覆盖。

导入只生成草稿，仍需校验发布。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from natural_work.store import ConflictError

PACK_DIR = Path(__file__).resolve().parent / 'presets'
FAMILY = {'postgresql': 'postgres', 'tbase': 'postgres', 'kingbase': 'postgres', 'dameng': 'oracle', 'oracle': 'oracle',
          'sqlite': 'sqlite', 'mysql': 'mysql'}


def _packs():
    packs = {}
    for path in sorted(PACK_DIR.glob('*.json')):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        meta = value.get('pack') or {}
        if value.get('format') == 'ask-data-business-template' and isinstance(meta.get('id'), str):
            packs[meta['id']] = value
    return packs


def _bare(name):
    return str(name).split('.')[-1].casefold()


def _resolve_tables(pack, source, authorized, *, unique=False):
    """Pack table name → actual authorized table entry."""
    tables = {}
    for table in source.get('tables', []):
        if table['name'] in authorized:
            tables.setdefault(_bare(table['name']), []).append(table)
    wanted = [t for s in pack.get('sources', []) for t in s.get('tables', [])]
    return {name: matches[-1] for name in wanted if (matches := tables.get(_bare(name)))
            and (not unique or len(matches) == 1)}


def _column(table, name):
    return next((c['name'] for c in table.get('columns', []) if c['name'].casefold() == str(name).casefold()), None)


META_FIELDS = ('group', 'group_summary', 'aliases')  # lookup only: never change what a metric computes


def _digest(row):
    keep = {k: v for k, v in row.items() if k not in ('id', 'origin', 'origin_digest')}
    return hashlib.sha256(json.dumps(keep, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _key_mapping(row):
    return 'mapping|' + row['kind'] + '|' + _bare(row['table']) + '|' + str(row.get('column') or '').casefold()


def _key_term(row):
    spec = row.get('config') or {}
    return ('metric|' + spec['id']) if spec.get('kind') == 'semantic-metric' else ('term|' + str(row.get('name', '')))


def _key_example(row):
    return 'example|' + str(row.get('question', ''))


def pack_keys(pack):
    """Stable item keys of a pack version (used to tell new items from ones an admin removed)."""
    return sorted({_key_mapping(m) for m in pack.get('mappings', [])} | {_key_term(t) for t in pack.get('terms', [])} | {_key_example(e) for e in pack.get('examples', [])})


def _stable_id(prefix, kind, key):
    return prefix + kind + '-' + hashlib.sha256(key.encode()).hexdigest()[:12]


def list_packs(engine, project):
    """Packs shipped with the plugin, with how well each matches the project's draft sources."""
    inventory = {s['id']: s for s in engine.config().get('sources', [])}
    result = []
    for pack_id, pack in _packs().items():
        wanted = [t for s in pack.get('sources', []) for t in s.get('tables', [])]
        matches = []
        for sid in project['draft']['source_ids']:
            source = inventory.get(sid)
            if not source:
                continue
            found = _resolve_tables(pack, source, set(project['draft']['tables'].get(sid, [])))
            if found:
                matches.append({'source_id': sid, 'name': source.get('name', sid), 'matched_tables': len(found), 'missing_tables': [t for t in wanted if t not in found]})
        meta = pack['pack']
        imported = sum(1 for g in ('terms', 'examples') for r in project['draft'].get(g, []) if str(r.get('origin', '')).startswith('pack:' + pack_id + '@'))
        result.append({'id': pack_id, 'name': meta.get('name', pack_id), 'version': meta.get('version', ''), 'business': meta.get('business', ''),
                       'description': meta.get('description', ''), 'tables': len(wanted), 'metrics': sum(1 for t in pack.get('terms', []) if (t.get('config') or {}).get('kind') == 'semantic-metric'),
                       'terms': sum(1 for t in pack.get('terms', []) if not t.get('config')), 'examples': len(pack.get('examples', [])),
                       'mappings': len(pack.get('mappings', [])), 'pending': pack['pack'].get('pending', []), 'matches': sorted(matches, key=lambda m: -m['matched_tables']),
                       'imported_rows': imported})
    return {'packs': result}


def _observed(connections, source, table, column, filters):
    from sqlalchemy import text
    from .connections import read_connection
    from .semantic import q, table as quote_table
    import sqlglot
    params, where = {}, []
    for i, f in enumerate(filters):
        names = []
        for j, v in enumerate(f['values']):
            params[f'f{i}_{j}'] = v
            names.append(f':f{i}_{j}')
        op = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}.get(f['op'])
        where.append(q(f['column']) + (' IN (' + ','.join(names) + ')' if f['op'] == 'in' else op + names[0]))
    sql = 'SELECT MIN(' + q(column) + ') AS first_value, MAX(' + q(column) + ') AS latest_value FROM ' + quote_table(table) + (' WHERE ' + ' AND '.join(where) if where else '')
    dialect = FAMILY.get(source['dialect'], source['dialect'])
    sql = sqlglot.parse_one(sql, read='postgres').sql(dialect=dialect)
    with read_connection(source, connections.url(source)) as conn:
        row = conn.execute(text(sql), params).mappings().one()
    return row['first_value'], row['latest_value']


def _periods(first, last, fmt):
    """YYYY and YYYY-MM periods between the observed bounds (inclusive)."""
    def parse(v):
        s = str(v).strip()
        if fmt in ('yyyymm', 'yyyy') or re.fullmatch(r'\d{4,6}', s):
            digits = re.sub(r'\D', '', s)
            return (int(digits[:4]), int(digits[4:6]) if len(digits) >= 6 else None)
        m = re.match(r'(\d{4})(?:-(\d{1,2}))?', s)
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else None
    a, b = parse(first), parse(last)
    if not a or not b:
        return []
    if any(not 2000 <= y <= 2099 or (m is not None and not 1 <= m <= 12) for y, m in (a, b)):
        raise ValueError('口径期间值无效：年份须为 2000—2099，月份须为 01—12，请核对源数据。')
    if (a[0], a[1] or 1) > (b[0], b[1] or 12):
        raise ValueError('口径期间起止顺序无效，请核对源数据。')
    years = [str(y) for y in range(a[0], b[0] + 1)]
    if fmt == 'yyyy' or a[1] is None or b[1] is None:
        return years
    months = []
    y, m = a
    while (y, m) <= b:
        months.append(f'{y}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return years + months[-288:]


def _rewrite_sql(sql, names, dialect):
    """Replace pack table names with the database's actual (possibly schema-qualified) names."""
    import sqlglot
    tree = sqlglot.parse_one(sql, read=dialect)
    for node in tree.find_all(sqlglot.exp.Table):
        actual = names.get(node.name.casefold())
        if actual and not node.args.get('db'):
            replacement = sqlglot.parse_one('SELECT 1 FROM ' + '.'.join('"' + p.replace('"', '""') + '"' for p in actual.split('.')), read=dialect).find(sqlglot.exp.Table)
            replacement.set('alias', node.args.get('alias'))
            node.replace(replacement)
    return tree.sql(dialect=dialect)


def import_pack(engine, business, connections, actor, args, *, published_config=None, business_snapshot=None):
    """Return (draft, report) for business.update('draft'). Reads only aggregate time bounds."""
    pack_id, sid = args.get('pack'), args.get('source_id')
    pack = _packs().get(pack_id)
    if not pack:
        raise ValueError('PACK_NOT_FOUND: 插件中没有这个业务口径包。')
    replace_calibers = args.get('replace_calibers') is True
    config = business.config() if business_snapshot is None else business_snapshot
    project = business.require(config, actor, args.get('business_project'), 'admin' if replace_calibers else 'editor')
    draft = copy.deepcopy(project['draft'] if published_config is None else published_config)
    if sid not in draft['source_ids']:
        raise ValueError('PACK_BINDING: 请选择当前项目已接入的数据源；导入不会新增授权。')
    if replace_calibers:
        for group in ('mappings', 'terms', 'examples', 'skills', 'layers'):
            draft[group] = []
    catalog = {source['id']: source for source in engine.config().get('sources', [])}
    source = catalog.get(sid)
    if not source or not source.get('connection_enabled', True):
        raise ValueError('PACK_BINDING: 数据源不可用，请先在数据源连接中启用。')
    found = _resolve_tables(pack, source, set(draft['tables'].get(sid, [])), unique=published_config is not None)
    if not found:
        raise ValueError('PACK_NO_MATCH: 当前数据源的授权表中没有该口径包需要的表，请先授权相关表。')
    if replace_calibers and any(table not in found for item in pack.get('sources', []) for table in item.get('tables', [])):
        raise ValueError('PACK_NO_MATCH: 全量替换要求内置口径所需表全部已授权。')
    names = {_bare(k): v['name'] for k, v in found.items()}
    meta = pack['pack']
    # 1.1.0: upgrading an applied built-in pack only adds items that are new in this version.
    # Items the previous version had but the project no longer has were removed by an admin
    # and are not added back; items still present are left exactly as they are.
    upgrade_from = None if replace_calibers else args.get('upgrade_from')
    old_keys = set((meta.get('history') or {}).get(str(upgrade_from), [])) if upgrade_from else set()
    repair_keys = set(meta.get('repair_keys') or [])
    retired_keys = set(meta.get('retired_keys') or [])
    origin = 'pack:' + pack_id + '@' + str(meta.get('version', ''))
    prefix = 'pack-' + re.sub(r'[^a-zA-Z0-9-]', '', pack_id)[:40] + '-'
    report = {'pack': pack_id, 'version': meta.get('version', ''), 'source_id': sid, 'matched_tables': sorted(names.values()),
              'missing_tables': [t for s in pack.get('sources', []) for t in s.get('tables', []) if t not in found],
              'imported': {'mappings': 0, 'terms': 0, 'metrics': 0, 'examples': 0}, 'skipped': [], 'kept': [], 'coverage': {},
              'upgrade_from': upgrade_from or None, 'removed_by_admin': [], 'enriched': [], 'retired': []}
    skip = lambda kind, name, reason: report['skipped'].append({'kind': kind, 'name': name, 'reason': reason})

    def retire(rows, key, label):
        # An upgrade drops the pack's own unedited rows that this version withdrew; edited rows stay.
        if not upgrade_from:
            return rows
        left = []
        for r in rows:
            obsolete = key(r) in retired_keys or upgrade_from in meta.get('replaces_versions', [])
            if obsolete and str(r.get('origin', '')).startswith('pack:' + pack_id + '@'):
                if r.get('origin_digest') == _digest(r):
                    report['retired'].append(label(r))
                    continue
                report['kept'].append(label(r))
            left.append(r)
        return left

    def column_of(table, col):
        t = found.get(table) or next((v for k, v in found.items() if _bare(k) == _bare(table)), None)
        return (t['name'], _column(t, col)) if t else (None, None)

    # ---- mappings: tables, fields, enums, time, relations ----
    # Existing mappings are authoritative, including older packs without digests.
    # Reimport fills missing entries; it never replaces an administrator's mapping.
    mappings = list(draft.get('mappings', []))
    present = {_key_mapping(m) for m in mappings if m.get('source_id') == sid}
    taken = {(m['kind'], m['source_id'], m['table'], m.get('column', '')) for m in mappings}
    for n, raw in enumerate(pack.get('mappings', []), 1):
        row = copy.deepcopy(raw)
        mkey = _key_mapping(raw)
        if mkey in present:
            report['kept'].append(raw.get('name', mkey))
            continue
        if upgrade_from and mkey in old_keys:
            report['removed_by_admin'].append(raw.get('name', ''))
            continue
        table, column = column_of(row['table'], row.get('column') or '')
        if not table:
            skip('映射', row.get('name', ''), '表不在当前授权目录：' + row['table'])
            continue
        if row['kind'] not in ('table', 'relation') and not column:
            skip('映射', row.get('name', ''), '字段不在当前目录：' + row['table'] + '.' + str(row.get('column')))
            continue
        row.update(id=_stable_id(prefix, 'm', sid + '|' + mkey), source_id=sid, table=table, column=column if row['kind'] not in ('table', 'relation') else '')
        if row['kind'] == 'relation':
            target, _ = column_of(row['to_table'], '')
            cols = [column_of(raw['table'], c)[1] for c in row['from_columns']]
            tcols = [column_of(raw['to_table'], c)[1] for c in row['to_columns']]
            if not target or None in cols or None in tcols:
                skip('关联', row.get('name', ''), '关联表或字段不在当前目录')
                continue
            tmeta = next(t for t in source['tables'] if t['name'] == target)
            keys = tmeta.get('unique_keys', []) + ([tmeta['primary_key']] if tmeta.get('primary_key') else [])
            if not any({k.casefold() for k in key} == {c.casefold() for c in tcols} for key in keys):
                skip('关联', row.get('name', ''), '关联目标字段在库中没有主键或唯一约束，不能保证不重复统计')
                continue
            row.update(to_table=target, from_columns=cols, to_columns=tcols)
        key = (row['kind'], sid, row['table'], row.get('column', ''))
        if key in taken:
            skip('映射', row.get('name', ''), '已有同一字段的手工配置，保留原配置')
            continue
        taken.add(key)
        mappings.append(row)
        report['imported']['mappings'] += 1
    draft['mappings'] = mappings

    # ---- terms and semantic metrics ----
    inventory_revision = source.get('target_revision', 0)
    original_terms = {_key_term(r): r for r in draft['terms']}
    draft['terms'] = retire(draft['terms'], _key_term, lambda r: r.get('name', ''))
    old = {r['id']: r for r in draft['terms'] if str(r.get('origin', '')).startswith('pack:' + pack_id + '@')}
    if upgrade_from:
        kept = set()
        by_key = {_key_term(r): r for r in draft['terms']}
        present = set(by_key)
    else:
        kept = {rid for rid, r in old.items() if r.get('origin_digest') and r['origin_digest'] != _digest(r)}
        present = set()
        draft['terms'] = [r for r in draft['terms'] if r['id'] not in old or r['id'] in kept]
    kept_keys = {_key_term(old[rid]) for rid in kept}
    for n, raw in enumerate(pack.get('terms', []), 1):
        row = copy.deepcopy(raw)
        term_dependency = row.pop('scope_from_term', None)
        dependency = row.pop('scope_from_key', None) or ('term|' + term_dependency if term_dependency else None)
        if dependency:
            key = dependency
            previous_term = original_terms.get(key)
            if ((previous_term and (not str(previous_term.get('origin', '')).startswith('pack:' + pack_id + '@')
                    or previous_term.get('origin_digest') != _digest(previous_term)))
                    or (upgrade_from and key in old_keys and previous_term is None)):
                skip('指标', row.get('name', ''), '相关业务术语已由管理员修改或删除，需人工确认结构化口径。')
                continue
        tkey = _key_term(raw)
        rid = _stable_id(prefix, 't', tkey)
        tables = row.pop('tables', None)
        grain = row.pop('coverage_grain', None)
        repairing = False
        if upgrade_from and tkey in present:
            # A row the admin never edited gets the pack's new lookup fields (group, code, aliases);
            # nothing else changes, and an edited row is left as it is.
            cur = by_key[tkey]
            pristine = str(cur.get('origin', '')).startswith('pack:' + pack_id + '@') and cur.get('origin_digest') == _digest(cur)
            repairing = tkey in repair_keys and pristine
            extra = {k: row[k] for k in META_FIELDS if k in row and k not in cur}
            if extra and pristine and not repairing:
                cur.update(copy.deepcopy(extra))
                cur['origin_digest'] = _digest(cur)
                report['enriched'].append(row.get('name', ''))
            if not repairing: continue
        if upgrade_from and tkey in old_keys and not repairing:
            report['removed_by_admin'].append(row.get('name', ''))
            continue
        if tkey in kept_keys:
            report['kept'].append(row.get('name', ''))
            continue
        if tables and not any(column_of(t, '')[0] for t in tables):
            skip('术语', row.get('name', ''), '相关表都不在当前目录')
            continue
        spec = row.get('config')
        if isinstance(spec, dict) and spec.get('kind') == 'semantic-metric':
            spec = copy.deepcopy(spec)
            missing = []
            for o in spec['operands']:
                t, _ = column_of(o['table'], '')
                if not t:
                    missing.append(o['table'])
                    continue
                for field in ('column', 'time_column'):
                    if o.get(field):
                        c = column_of(o['table'], o[field])[1]
                        o[field] = c if c else missing.append(o['table'] + '.' + o[field])
                o['entity_columns'] = [column_of(o['table'], c)[1] or missing.append(c) for c in o.get('entity_columns', [])]
                for f in o.get('filters', []):
                    f['column'] = column_of(o['table'], f['column'])[1] or missing.append(f['column'])
                latest=o.get('latest')
                if latest:
                    et=latest['entity_table']; rt=o['table']
                    def mapped(tb,c):
                        value=column_of(tb,c)[1]
                        if not value: missing.append(tb+'.'+c)
                        return value
                    latest['entity_key']=[mapped(et,c) for c in latest['entity_key']]
                    latest['report_key']=[mapped(rt,c) for c in latest['report_key']]
                    if latest.get('entity_year_column'): latest['entity_year_column']=mapped(et,latest['entity_year_column'])
                    for f in latest.get('entity_filters',[]): f['column']=mapped(et,f['column'])
                    for order in latest.get('tie_breakers',[]): order['column']=mapped(rt,order['column'])
                    latest['blank_defaults']={mapped(rt,c):v for c,v in latest.get('blank_defaults',{}).items()}
                    latest['allowed_values']={mapped(rt,c):v for c,v in latest.get('allowed_values',{}).items()}
                    latest['entity_table']=column_of(et,'')[0]
                    if not latest['entity_table']: missing.append(et)
                o['table'] = t
            for d in spec.get('dimensions', []):
                t, c = column_of(d['table'], d['column'])
                if not c:
                    missing.append(d['table'] + '.' + d['column'])
                d['table'], d['column'] = t, c
            for r in spec.get('relations', []):
                ft, tt = column_of(r['from_table'], '')[0], column_of(r['to_table'], '')[0]
                fc = [column_of(r['from_table'], c)[1] for c in r['from_columns']]
                tc = [column_of(r['to_table'], c)[1] for c in r['to_columns']]
                if not ft or not tt or None in fc or None in tc:
                    missing.append(r['from_table'] + '→' + r['to_table'])
                r.update(from_table=ft, to_table=tt, from_columns=fc, to_columns=tc)
            if missing:
                skip('指标', row.get('name', ''), '缺少表或字段：' + '、'.join(str(m) for m in missing if m))
                continue
            spec['source_id'] = sid
            spec['source_revision'] = inventory_revision
            if spec.get('time_mode', 'period') == 'period' and not any(o.get('latest') for o in spec['operands']):
                periods = None
                try:
                    coverage_operands = spec['operands']
                    # For the inspection rates, zero matches for "passed" is a possible
                    # outcome. The inspected population establishes the available years.
                    if pack_id == 'chdl-2026' and spec['id'] in ('inspection_pass_rate','result_quality_pass_rate'):
                        coverage_operands = [o for o in coverage_operands if o['id']=='total']
                    for o in coverage_operands:
                        first, last = _observed(connections, source, o['table'], o['time_column'], o.get('filters', []))
                        found_periods = set(_periods(first, last, o.get('time_format')))
                        periods = found_periods if periods is None else periods & found_periods
                except Exception:
                    skip('指标', row.get('name', ''), '读取时间范围失败，请检查连接和字段类型')
                    continue
                if grain == 'month':
                    periods = {p for p in periods if len(p) == 7}
                elif grain == 'year':
                    periods = {p for p in periods if len(p) == 4}
                if not periods:
                    skip('指标', row.get('name', ''), '库中没有该指标的数据')
                    continue
                spec['coverage'] = sorted(periods)
                report['coverage'][row.get('name', '')] = spec['coverage'][0] + ' 至 ' + spec['coverage'][-1]
            try:
                from .semantic import validate
                validate(spec, {**source, 'tables': [t for t in source['tables'] if t['name'] in draft['tables'][sid]]})
            except Exception as error:
                skip('指标', row.get('name', ''), str(error))
                continue
            row['config'] = spec
            report['imported']['metrics'] += 1
        else:
            if isinstance(spec, dict) and spec.get('kind') == 'metric-limitation':
                row['config'] = {**spec, 'source_id': sid}
            if isinstance(spec, dict) and spec.get('kind') == 'row-count-aggregation':
                from .row_count_aggregation import validate
                table = spec['table']
                bound, _ = column_of(table, '')
                if bound:
                    config = {**spec, 'source_id': sid, 'source_revision': inventory_revision, 'table': bound,
                              'dimension': column_of(table, spec['dimension'])[1],
                              'time_column': column_of(table, spec['time_column'])[1],
                              'filters': {column_of(table, key)[1]: values for key, values in spec['filters'].items()}}
                    try:
                        validate(config, source)
                    except ValueError as error:
                        if published_config is not None:
                            skip('术语', row.get('name', ''), str(error))
                            continue
                        row.pop('config')
                    else:
                        row['config'] = config
                else:
                    if published_config is not None:
                        skip('术语', row.get('name', ''), '结构化口径所需表未授权：' + table)
                        continue
                    row.pop('config')
            report['imported']['terms'] += 1
        row.update(id=rid, origin=origin, source_id=sid)
        row['origin_digest'] = _digest(row)
        if repairing:
            draft['terms'] = [r for r in draft['terms'] if r['id'] != cur['id']]
            report.setdefault('repaired', []).append(row.get('name', ''))
        draft['terms'].append(row)

    # ---- query examples (dialect variants, actual table names) ----
    from .vendor.plans import validate_plan, PolicyContext
    import uuid
    family = FAMILY.get(source['dialect'], source['dialect'])
    draft['examples'] = retire(draft['examples'], _key_example, lambda r: r['question'])
    old = {r['id']: r for r in draft['examples'] if str(r.get('origin', '')).startswith('pack:' + pack_id + '@')}
    if upgrade_from:
        kept = set()
        present = {_key_example(r) for r in draft['examples']}
    else:
        kept = {rid for rid, r in old.items() if r.get('origin_digest') and r['origin_digest'] != _digest(r)}
        present = set()
        draft['examples'] = [r for r in draft['examples'] if r['id'] not in old or r['id'] in kept]
    kept_keys = {_key_example(old[rid]) for rid in kept}
    for n, raw in enumerate(pack.get('examples', []), 1):
        ekey = _key_example(raw)
        rid = _stable_id(prefix, 'e', ekey)
        previous = next((r for r in draft['examples'] if _key_example(r)==ekey), None)
        repairing = bool(upgrade_from and ekey in repair_keys and previous
                         and str(previous.get('origin','')).startswith('pack:'+pack_id+'@')
                         and previous.get('origin_digest') == _digest(previous))
        if upgrade_from and ekey in present and not repairing:
            continue
        if upgrade_from and ekey in old_keys and not repairing:
            report['removed_by_admin'].append(raw['question'])
            continue
        if ekey in kept_keys:
            report['kept'].append(raw['question'])
            continue
        tables = raw.get('tables') or []
        if tables and not all(column_of(t, '')[0] for t in tables):
            skip('示例', raw['question'], '示例用到的表不在当前目录')
            continue
        sql = (raw.get('variants') or {}).get(family) or raw['logical_sql']
        try:
            sql = _rewrite_sql(sql, names, family)
            plan = {'mode': 'sql', 'plan_id': uuid.uuid4().hex, 'workspace_id': 'publish', 'datasource_ref': sid, 'logical_sql': sql, 'parameters': [],
                    'expected_chart': {'type': 'table', 'title': '校验'}, 'limit': 1}
            validate_plan(plan, PolicyContext(workspace_id='publish', allowed_datasource_refs=[sid], allowed_relations=draft['tables'][sid], db_types={sid: source['dialect']}))
        except Exception as error:
            skip('示例', raw['question'], '校验未通过：' + str(error)[:160])
            continue
        row = {'id': rid, 'question': raw['question'], 'logical_sql': sql, 'source_id': sid, 'origin': origin}
        row['origin_digest'] = _digest(row)
        if repairing:
            draft['examples'] = [r for r in draft['examples'] if r['id'] != previous['id']]
            report.setdefault('repaired', []).append(row['question'])
        draft['examples'].append(row)
        report['imported']['examples'] += 1
    report['pending'] = list(meta.get('pending', []))
    # A partial schema import can omit some metrics. Keep the original rule as
    # a review draft instead of publishing dangling choices that can never run.
    from .definition_evidence import scoped_terms
    from .business_ambiguity import reference_issues
    scoped={**source,'tables':[t for t in source['tables'] if t['name'] in draft['tables'][sid]],'terms':scoped_terms(draft,sid,catalog)}
    for issue in reference_issues(scoped):
        row=next((t for t in draft['terms'] if t['id']==issue['term_id']),None)
        if row and row.get('clarification') and str(row.get('origin','')).startswith('pack:'+pack_id+'@'):
            pristine=row.get('origin_digest')==_digest(row)
            row['pending_clarification']=row.pop('clarification')
            row['clarification_review_reason']=issue['message']+' '+','.join(issue['metric_ids'])
            if pristine:row['origin_digest']=_digest(row)
            report['pending'].append(row['name']+'：'+row['clarification_review_reason'])
    if replace_calibers and (report['skipped'] or not any(report['imported'].values())):
        details = '；'.join(f"{item['name']}：{item['reason']}" for item in report['skipped'][:3])
        raise ValueError('PACK_REPLACEMENT_INCOMPLETE: 内置口径未能完整导入，当前配置草稿已保留。' + details)
    return draft, report


# A partial match can suggest a review draft. Automatic activation requires
# a complete binding to one source already authorized in the live project.
BUILTIN_MATCH_SHARE = 0.5


def _builtin_candidate(engine, config, pack, *, activate=False):
    wanted = [t for s in pack.get('sources', []) for t in s.get('tables', [])]
    inventory = {s['id']: s for s in engine.config().get('sources', [])}
    candidates = []
    threshold = len(wanted) if activate else max(1, len(wanted) * BUILTIN_MATCH_SHARE)
    for sid in config['source_ids']:
        source = inventory.get(sid)
        if not source or not source.get('connection_enabled', True):
            continue
        found = _resolve_tables(pack, source, set(config['tables'].get(sid, [])), unique=activate)
        if wanted and len(found) >= threshold:
            candidates.append((source, len(found)))
    if not candidates or (activate and len(candidates) != 1):
        return None
    return max(candidates, key=lambda item: item[1])[0]


def apply_builtin_packs(engine, business, connections, only=None, log=None):
    """Apply approved bundled definitions and stage all other matching packs."""
    done = []

    def record(row):
        done.append(row)
        if log:
            log(row)

    for pack_id, pack in _packs().items():
        source_identity = engine._config_identity()
        cfg = business.current_snapshot()
        project_ids = [only] if only else list(cfg['projects'])
        version = str(pack['pack'].get('version', ''))
        activate = pack['pack'].get('activate_on_match') is True
        updates = {}
        outcomes = {}
        for bid in project_ids:
            project = cfg['projects'].get(bid)
            if project is None:
                continue
            if project.get('archived') or not project.get('published_version'):
                continue
            if (project.get('builtin_packs') or {}).get(pack_id) == version:
                continue
            pending = project.get('pending_builtin_packs', {}).get(pack_id, {})
            if pending and (pending.get('actor') != 'system:builtin-pack'
                            or (not activate and pending.get('version') == version)):
                continue
            published = next((v['config'] for v in project['versions'] if v['version'] == project['published_version']), None)
            if published is None:
                continue
            if not activate and published != project['draft']:
                record({'project': bid, 'pack': pack_id, 'skipped': '有未发布的草稿修改，发布后再应用'})
                continue
            source = _builtin_candidate(engine, published, pack, activate=activate)
            if not source:
                continue
            sid = source['id']
            actor = next((user for user, role in project['members'].items() if role == 'admin'), None)
            if not actor:
                continue
            previous = (project.get('builtin_packs') or {}).get(pack_id)
            try:
                draft, report = import_pack(
                    engine, business, connections, actor,
                    {'pack': pack_id, 'source_id': sid, 'business_project': bid, 'upgrade_from': previous},
                    published_config=published if activate else None,
                    business_snapshot=cfg,
                )
                if activate and (report['missing_tables'] or report['skipped']):
                    problem = report['missing_tables'] or [row['name'] for row in report['skipped']]
                    raise ValueError('PACK_BINDING_INCOMPLETE: 当前授权目录无法完整绑定内置业务口径。' + '、'.join(problem[:3]))
                note = '内置业务口径包 ' + pack['pack'].get('name', pack_id) + ' ' + version + ('（由 ' + previous + ' 升级）' if previous else '')
                staged = business.prepare_builtin_pack(project, draft, pack_id, version, note,
                                                       source_id=sid, activate=activate)
                if staged is None:
                    continue
                updates[bid] = project, staged
                outcomes[bid] = {'project': bid, 'pack': pack_id, 'source_id': sid, 'imported': report['imported'],
                                 'skipped_rows': len(report['skipped']), 'state': 'active' if activate else 'pending_review',
                                 'published': activate, 'matched_tables': report['matched_tables'],
                                 **({'upgraded_from': previous, 'removed_by_admin': report['removed_by_admin'],
                                     'enriched': report['enriched'], 'retired': report['retired']} if previous else {})}
            except Exception as error:  # A pack must never break the engine or a publish.
                record({'project': bid, 'pack': pack_id, 'error': str(error)[:300]})
        try:
            applied = business.commit_builtin_packs(updates, source_identity)
        except Exception as error:
            for bid in updates:
                record({'project': bid, 'pack': pack_id, 'error': str(error)[:300]})
            continue
        for bid, outcome in outcomes.items():
            record(outcome if bid in applied else {
                'project': bid, 'pack': pack_id, 'error': '口径导入期间配置已改变，已保留当前修改。',
            })
    return done
