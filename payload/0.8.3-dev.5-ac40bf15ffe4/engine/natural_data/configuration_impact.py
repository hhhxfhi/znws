"""Real configuration dependency and impact analysis for business metadata changes.

Evaluates precise impacts on metrics, ordinary terms, examples/representative questions,
and historical validation records based on SQL AST alias-aware column dependencies,
and semantic metric contracts. Pure read-only; never mutates configurations.
"""
from __future__ import annotations
import copy
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlglot import parse_one, exp
from .business_sql_checks import DIALECTS


def _parse_sql_dependencies(
    sql: str, dialect: str
) -> Tuple[Set[str], Set[Tuple[str, str]], Set[str], bool, bool, bool]:
    """Extract AST alias-aware dependencies.
    
    Returns:
        (physical_tables, exact_qualified_columns, ambiguous_unqualified_columns,
         has_star, has_unresolved_cte_or_subquery, parse_ok)
    """
    read_dialect = DIALECTS.get(dialect, dialect or None)
    try:
        ast = parse_one(sql, read=read_dialect)

        # 1. CTE names
        cte_names = {cte.alias_or_name for cte in ast.find_all(exp.CTE) if cte.alias_or_name}

        # 2. Subquery aliases
        subquery_aliases = {sub.alias for sub in ast.find_all(exp.Subquery) if sub.alias}

        # 3. Physical tables and alias mapping
        alias_to_table: Dict[str, str] = {}
        physical_tables: Set[str] = set()
        cte_aliases: Set[str] = set()

        for t in ast.find_all(exp.Table):
            real_name = '.'.join(p.name for p in t.parts)
            # Exclude CTE names from physical tables
            if real_name in cte_names or real_name.split('.')[-1] in cte_names:
                if t.alias:
                    cte_aliases.add(t.alias)
                cte_aliases.add(real_name)
                continue
            physical_tables.add(real_name)
            if t.alias:
                alias_to_table[t.alias] = real_name
            alias_to_table[real_name] = real_name
            if '.' in real_name:
                base_t = real_name.split('.')[-1]
                alias_to_table.setdefault(base_t, real_name)

        # 4. Alias-aware column resolution
        exact_qualified_columns: Set[Tuple[str, str]] = set()
        ambiguous_unqualified_columns: Set[str] = set()
        has_unresolved_cte_or_subquery = bool(cte_names or subquery_aliases)

        for c in ast.find_all(exp.Column):
            col_name = c.name
            col_table = c.table
            if col_table:
                if col_table in alias_to_table:
                    # Explicit alias resolves directly to a known physical table
                    exact_qualified_columns.add((alias_to_table[col_table], col_name))
                elif col_table in cte_aliases or col_table in cte_names or col_table in subquery_aliases:
                    has_unresolved_cte_or_subquery = True
                else:
                    has_unresolved_cte_or_subquery = True
            else:
                # Unqualified column
                if len(physical_tables) == 1:
                    # Single physical table: column unambiguously belongs to this table
                    single_t = next(iter(physical_tables))
                    exact_qualified_columns.add((single_t, col_name))
                else:
                    # Multi-table query: unqualified column is ambiguous
                    ambiguous_unqualified_columns.add(col_name)

        has_star = bool(list(ast.find_all(exp.Star)))
        if has_unresolved_cte_or_subquery:
            # Aliases are scoped per SELECT. A flat alias map cannot prove
            # column ownership across nested SELECTs that reuse an alias.
            ambiguous_unqualified_columns.update(c for _, c in exact_qualified_columns)
            exact_qualified_columns.clear()
        return physical_tables, exact_qualified_columns, ambiguous_unqualified_columns, has_star, has_unresolved_cte_or_subquery, True
    except Exception:
        return set(), set(), set(), False, False, False


def diff_configuration(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute structural and semantic diff between two configuration states."""
    before = before or {}
    after = after or {}

    before_sources = set(before.get('source_ids', []))
    after_sources = set(after.get('source_ids', []))
    revoked_sources = before_sources - after_sources
    added_sources = after_sources - before_sources

    revoked_tables: Dict[str, Set[str]] = {}
    added_tables: Dict[str, Set[str]] = {}
    for sid in before_sources | after_sources:
        old_tables = set(before.get('tables', {}).get(sid, [])) if sid in before_sources else set()
        new_tables = set(after.get('tables', {}).get(sid, [])) if sid in after_sources else set()
        if old_tables - new_tables:
            revoked_tables[sid] = old_tables - new_tables
        if new_tables - old_tables:
            added_tables[sid] = new_tables - old_tables

    old_mappings = {m['id']: m for m in before.get('mappings', []) if isinstance(m, dict) and 'id' in m}
    new_mappings = {m['id']: m for m in after.get('mappings', []) if isinstance(m, dict) and 'id' in m}

    changed_columns: Dict[Tuple[str, str], Set[str]] = {}  # (sid, table) -> set(columns)
    changed_relations: Set[str] = set()  # relation mapping ids (strings)
    changed_relation_pairs: Set[Tuple[str, str, str]] = set()  # (sid, table1, table2)
    changed_table_mappings: Set[Tuple[str, str]] = set()  # (sid, table)

    all_mapping_ids = set(old_mappings) | set(new_mappings)
    for mid in all_mapping_ids:
        m_old = old_mappings.get(mid)
        m_new = new_mappings.get(mid)
        if m_old == m_new:
            continue

        # Inspect BOTH m_old and m_new to prevent missing newly targeted tables/columns
        for m in (m_old, m_new):
            if not m or not isinstance(m, dict):
                continue
            sid = m.get('source_id')
            table = m.get('table') or m.get('from_table')
            kind = m.get('kind')
            to_table = m.get('to_table')

            if kind == 'relation':
                changed_relations.add(mid)
                if sid and table and to_table:
                    changed_relation_pairs.add((sid, table, to_table))
                    changed_relation_pairs.add((sid, to_table, table))
                for c in (m.get('from_columns') or []):
                    if sid and table and c:
                        changed_columns.setdefault((sid, table), set()).add(c)
                for c in (m.get('to_columns') or []):
                    if sid and to_table and c:
                        changed_columns.setdefault((sid, to_table), set()).add(c)
            elif kind in ('field', 'term', 'enum', 'time'):
                col = m.get('column')
                if sid and table and col:
                    changed_columns.setdefault((sid, table), set()).add(col)
            elif kind == 'table':
                if sid and table:
                    changed_table_mappings.add((sid, table))

    old_terms = {t['id']: t for t in before.get('terms', []) if isinstance(t, dict) and 'id' in t}
    new_terms = {t['id']: t for t in after.get('terms', []) if isinstance(t, dict) and 'id' in t}

    modified_metrics: Dict[str, Dict[str, Any]] = {}
    removed_metrics: Dict[str, Dict[str, Any]] = {}
    modified_terms: Dict[str, Dict[str, Any]] = {}
    removed_terms: Dict[str, Dict[str, Any]] = {}

    for tid, t in old_terms.items():
        if t.get('config', {}).get('kind') == 'semantic-metric':
            if tid not in new_terms:
                removed_metrics[tid] = t
            elif new_terms[tid] != t:
                modified_metrics[tid] = new_terms[tid]
        else:
            # Ordinary business terms / aliases / definitions
            if tid not in new_terms:
                removed_terms[tid] = t
            elif new_terms[tid] != t:
                modified_terms[tid] = new_terms[tid]

    for tid, term in new_terms.items():
        if tid not in old_terms and term.get('config', {}).get('kind') != 'semantic-metric':
            modified_terms[tid] = term

    old_examples = {e['id']: e for e in before.get('examples', []) if isinstance(e, dict) and 'id' in e}
    new_examples = {e['id']: e for e in after.get('examples', []) if isinstance(e, dict) and 'id' in e}

    modified_examples: Dict[str, Dict[str, Any]] = {}
    removed_examples: Dict[str, Dict[str, Any]] = {}
    for eid, e in old_examples.items():
        if eid not in new_examples:
            removed_examples[eid] = e
        elif new_examples[eid] != e:
            modified_examples[eid] = new_examples[eid]

    return {
        'revoked_sources': revoked_sources,
        'added_sources': added_sources,
        'revoked_tables': revoked_tables,
        'added_tables': added_tables,
        'changed_columns': changed_columns,
        'changed_relations': changed_relations,
        'changed_relation_pairs': changed_relation_pairs,
        'changed_table_mappings': changed_table_mappings,
        'modified_metrics': modified_metrics,
        'removed_metrics': removed_metrics,
        'modified_terms': modified_terms,
        'removed_terms': removed_terms,
        'modified_examples': modified_examples,
        'removed_examples': removed_examples,
        'old_mappings': old_mappings,
        'new_mappings': new_mappings,
    }


def compute_impact(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    validation_records: Optional[List[Dict[str, Any]]] = None,
    sources_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyze real impacts of configuration changes on metrics, terms, examples, and records."""
    diff = diff_configuration(before, after)
    known_impacts: List[Dict[str, Any]] = []
    pending_dependencies: List[Dict[str, Any]] = []
    unaffected_items: List[Dict[str, Any]] = []

    affected_item_ids: Dict[str, Dict[str, Any]] = {}
    pending_item_ids: Dict[str, Dict[str, Any]] = {}
    sources_info = sources_info or {}

    # 1. Ordinary Terms Impact
    for tid, term in diff['removed_terms'].items():
        impact = {
            'target_type': 'term',
            'id': tid,
            'name': term.get('name') or tid,
            'impact': 'deleted',
            'impact_label': '术语已删除',
            'reason': '业务术语已从配置中移除，相关检索与解释将失效。',
        }
        known_impacts.append(impact)
        affected_item_ids[tid] = impact

    for tid, term in diff['modified_terms'].items():
        impact = {
            'target_type': 'term',
            'id': tid,
            'name': term.get('name') or tid,
            'impact': 'modified',
            'impact_label': '术语定义变更',
            'reason': '业务术语名称、别名或口径说明已修改，可能影响意图识别与问数路线。',
        }
        known_impacts.append(impact)
        affected_item_ids[tid] = impact

    # 2. Semantic Metrics Impact
    for mid, metric in diff['removed_metrics'].items():
        impact = {
            'target_type': 'metric',
            'id': mid,
            'name': metric.get('name') or mid,
            'impact': 'deleted',
            'impact_label': '指标已删除',
            'reason': '指标已从配置中移除，相关问答与引用将失效。',
        }
        known_impacts.append(impact)
        affected_item_ids[mid] = impact

    for term in (after or {}).get('terms', []):
        if term.get('config', {}).get('kind') != 'semantic-metric':
            continue
        mid = term.get('id')
        name = term.get('name') or mid
        config = term.get('config', {})
        sid = config.get('source_id')

        reasons = []
        if mid in diff['modified_metrics']:
            reasons.append('指标定义本身已修改（计算公式、操作数或维度调整）')
        if sid in diff['revoked_sources']:
            reasons.append(f'所属数据源【{sid}】已被撤销授权')

        ref_tables = set()
        ref_cols = []

        for op in config.get('operands', []):
            t = op.get('table')
            if t:
                ref_tables.add(t)
            c = op.get('column')
            tc = op.get('time_column')
            if t and c and c in diff['changed_columns'].get((sid, t), set()):
                ref_cols.append(f'{t}.{c}')
            if t and tc and tc in diff['changed_columns'].get((sid, t), set()):
                ref_cols.append(f'{t}.{tc}')
            for flt in (op.get('filters') or []):
                fc = flt.get('column') if isinstance(flt, dict) else None
                if t and fc and fc in diff['changed_columns'].get((sid, t), set()):
                    ref_cols.append(f'{t}.{fc}')
            for ec in (op.get('entity_columns') or []):
                if t and ec in diff['changed_columns'].get((sid, t), set()):
                    ref_cols.append(f'{t}.{ec}')

            latest = op.get('latest')
            if isinstance(latest, dict):
                et = latest.get('entity_table')
                if et:
                    ref_tables.add(et)
                    if et in diff['revoked_tables'].get(sid, set()):
                        reasons.append(f'最新报告关联的实体表【{et}】已被撤销授权')
                    for ek in latest.get('entity_key', []):
                        if ek in diff['changed_columns'].get((sid, et), set()):
                            ref_cols.append(f'{et}.{ek}')
                    for rk in latest.get('report_key', []):
                        if t and rk in diff['changed_columns'].get((sid, t), set()):
                            ref_cols.append(f'{t}.{rk}')
                    eyc = latest.get('entity_year_column')
                    if eyc and eyc in diff['changed_columns'].get((sid, et), set()):
                        ref_cols.append(f'{et}.{eyc}')
                    for ef in latest.get('entity_filters', []):
                        efc = ef.get('column') if isinstance(ef, dict) else None
                        if efc and efc in diff['changed_columns'].get((sid, et), set()):
                            ref_cols.append(f'{et}.{efc}')
                    for tb in latest.get('tie_breakers', []):
                        tbc = tb.get('column') if isinstance(tb, dict) else None
                        if t and tbc and tbc in diff['changed_columns'].get((sid, t), set()):
                            ref_cols.append(f'{t}.{tbc}')

        for dim in config.get('dimensions', []):
            t = dim.get('table')
            c = dim.get('column')
            if t:
                ref_tables.add(t)
            if t and c and c in diff['changed_columns'].get((sid, t), set()):
                ref_cols.append(f'{t}.{c}')

        rev_t = ref_tables & diff['revoked_tables'].get(sid, set())
        if rev_t:
            reasons.append(f'依赖的数据表【{", ".join(sorted(rev_t))}】已被撤销授权')

        for rel in config.get('relations', []):
            if isinstance(rel, dict):
                from_t = rel.get('from_table') or rel.get('table')
                to_t = rel.get('to_table')
                from_cols = rel.get('from_columns') or []
                to_cols = rel.get('to_columns') or []
                if (sid, from_t, to_t) in diff['changed_relation_pairs'] or (sid, to_t, from_t) in diff['changed_relation_pairs']:
                    reasons.append(f'依赖的表关联【{from_t} ↔ {to_t}】定义已变更')
                for c in from_cols:
                    if from_t and c in diff['changed_columns'].get((sid, from_t), set()):
                        ref_cols.append(f'{from_t}.{c}')
                for c in to_cols:
                    if to_t and c in diff['changed_columns'].get((sid, to_t), set()):
                        ref_cols.append(f'{to_t}.{c}')
            elif isinstance(rel, str):
                if rel in diff['changed_relations']:
                    reasons.append(f'依赖的表关联【{rel}】已变更或删除')

        if ref_cols:
            reasons.append(f'引用的字段【{", ".join(sorted(set(ref_cols)))}】业务映射/枚举已变更')

        if reasons:
            impact = {
                'target_type': 'metric',
                'id': mid,
                'name': name,
                'impact': 'invalidated',
                'impact_label': '指标口径受影响',
                'reason': '；'.join(reasons) + '。',
            }
            known_impacts.append(impact)
            affected_item_ids[mid] = impact
        else:
            unaffected_items.append({
                'target_type': 'metric',
                'id': mid,
                'name': name,
            })

    # 3. Examples / Representative Questions Impact
    for eid, example in diff['removed_examples'].items():
        impact = {
            'target_type': 'example',
            'id': eid,
            'name': example.get('question') or eid,
            'impact': 'deleted',
            'impact_label': '示例已删除',
            'reason': '示例已从配置中移除，无法再作为参考或试问依据。',
        }
        known_impacts.append(impact)
        affected_item_ids[eid] = impact

    all_changed_terms = {**diff['removed_terms'], **diff['modified_terms']}

    for example in (after or {}).get('examples', []):
        eid = example.get('id')
        question = example.get('question') or eid
        sid = example.get('source_id')
        sql = example.get('logical_sql', '')
        assumptions = str(example.get('assumptions', ''))

        dialect = (sources_info.get(sid) or {}).get('dialect', 'sqlite')
        (
            tables,
            exact_qualified_cols,
            ambiguous_unqualified_cols,
            has_star,
            has_unresolved_cte_or_subquery,
            parse_ok,
        ) = _parse_sql_dependencies(sql, dialect)

        reasons = []
        pending_reasons = []

        if eid in diff['modified_examples']:
            reasons.append('示例本身的问法、SQL 或独立预期已修改')
        if sid in diff['revoked_sources']:
            reasons.append(f'所属数据源【{sid}】已被撤销授权')

        # Check revoked physical tables
        rev_t = tables & diff['revoked_tables'].get(sid, set())
        if rev_t:
            reasons.append(f'查询涉及的数据表【{", ".join(sorted(rev_t))}】已被撤销授权')

        # 1) Exact qualified column dependency: b.amount matches ONLY (sid, 'orders', 'amount')
        exact_hits = []
        for t, c in exact_qualified_cols:
            if c in diff['changed_columns'].get((sid, t), set()):
                exact_hits.append(f'{t}.{c}')
        if exact_hits:
            reasons.append(f'查询引用的字段【{", ".join(sorted(set(exact_hits)))}】业务映射/枚举已变更')

        # 2) Unqualified column in multi-table queries: ambiguous column is pending, NOT false known influence
        ambiguous_hits = []
        for c in ambiguous_unqualified_cols:
            for t in tables:
                if c in diff['changed_columns'].get((sid, t), set()):
                    ambiguous_hits.append(f'{c}（涉及表 {t}）')
        if ambiguous_hits:
            pending_reasons.append(f'多表关联查询中使用了未限定表名的字段【{", ".join(sorted(set(ambiguous_hits)))}】，相关表映射有调整，需人工核对列归属')

        # 3) Star wildcards
        if has_star:
            mod_t = [t for t in tables if diff['changed_columns'].get((sid, t))]
            if mod_t:
                pending_reasons.append(f'SQL 使用了通配符（*），表【{", ".join(mod_t)}】字段映射有调整，需核对输出列')

        # 4) Changed relation pairs in SQL
        for sid_r, t1, t2 in diff['changed_relation_pairs']:
            if sid_r == sid and t1 in tables and t2 in tables:
                reasons.append(f'涉及的表间关联【{t1} ↔ {t2}】定义已变更')

        # 5) Semantic metric linkage
        metric_code = example.get('metric_code') or example.get('origin_metric')
        if metric_code:
            matching_metric_impact = next(
                (i for i in known_impacts if i['target_type'] == 'metric' and (i['id'] == metric_code or i['name'] == metric_code)),
                None,
            )
            if matching_metric_impact:
                reasons.append(f'关联的指标口径【{matching_metric_impact["name"]}】已发生变更')

        # 6) Unresolved CTE or subquery conservative unknown
        if has_unresolved_cte_or_subquery:
            source_has_mod = bool(diff['revoked_tables'].get(sid) or any(k[0] == sid for k in diff['changed_columns']))
            if source_has_mod:
                pending_reasons.append('查询涉及 CTE 或子查询，底层数据源映射有变动，无法静态证明字段安全，需人工核对')

        # 7) Unparsed SQL syntax error
        if not parse_ok:
            pending_reasons.append('SQL 语法复杂或未解析，无法证明依赖安全，需人工核对')

        # 8) Ordinary terms impact: proven vs ambiguous routing
        proven_terms = []
        ambiguous_terms = []
        term_refs = set(
            example.get('term_ids', [])
            or example.get('term_refs', [])
            or ([example.get('term_id')] if example.get('term_id') else [])
        )

        for tid, term in all_changed_terms.items():
            t_name = str(term.get('name', '')).strip()
            t_sid = term.get('source_id')
            if t_sid and sid and t_sid != sid:
                continue
            aliases = term.get('aliases', []) or term.get('synonyms', []) or ([term.get('code')] if term.get('code') else [])
            is_proven = False
            if tid in term_refs:
                is_proven = True
            elif t_name and len(t_name) >= 2 and (t_name in question or t_name in assumptions):
                is_proven = True
            elif any(a and len(str(a)) >= 2 and (str(a) in question or str(a) in assumptions) for a in aliases):
                is_proven = True

            if is_proven:
                proven_terms.append(t_name or tid)
            else:
                if not t_sid or not sid or t_sid == sid:
                    ambiguous_terms.append(t_name or tid)

        if proven_terms:
            reasons.append(f'关联的业务术语【{", ".join(proven_terms)}】定义/别名已发生变更，问答检索与口径路线需重新核对')
        elif ambiguous_terms:
            pending_reasons.append(f'同数据源业务术语【{", ".join(ambiguous_terms[:3])}{" 等" if len(ambiguous_terms) > 3 else ""}】发生变更，可能影响语义路由与问答理解，需人工核对')

        if reasons:
            impact = {
                'target_type': 'example',
                'id': eid,
                'name': question,
                'impact': 'invalidated',
                'impact_label': '示例需重新复测',
                'reason': '；'.join(reasons) + '。',
            }
            known_impacts.append(impact)
            affected_item_ids[eid] = impact
        elif pending_reasons:
            pending = {
                'target_type': 'example',
                'id': eid,
                'name': question,
                'impact_label': '待核对依赖',
                'reason': '；'.join(pending_reasons) + '。',
            }
            pending_dependencies.append(pending)
            pending_item_ids[eid] = pending
        else:
            unaffected_items.append({
                'target_type': 'example',
                'id': eid,
                'name': question,
            })

    # 4. Verification Records Impact
    if validation_records:
        for rec in validation_records:
            rid = rec.get('id')
            rkind = rec.get('kind', 'example')
            rquestion = rec.get('question') or rid
            rsid = rec.get('source_id')

            # Directly invalidate if associated data source is revoked
            if rsid and rsid in diff['revoked_sources']:
                known_impacts.append({
                    'target_type': 'verification_record',
                    'id': rid,
                    'name': f'验证记录：{rquestion}',
                    'impact': 'expired',
                    'impact_label': '数据源已撤销',
                    'reason': f'对应数据源【{rsid}】已被撤销授权，历史验证记录失效。',
                })
                continue

            if rkind in ('example', 'native_trial', 'native_trial_review'):
                ex_id = rec.get('example_id')
                if ex_id in affected_item_ids:
                    cause = affected_item_ids[ex_id]
                    known_impacts.append({
                        'target_type': 'verification_record',
                        'id': rid,
                        'name': f'验证记录：{rquestion}',
                        'impact': 'expired',
                        'impact_label': '验证记录已过期',
                        'reason': f'对应示例【{cause["name"]}】受影响：{cause["reason"]}',
                    })
                elif ex_id in pending_item_ids:
                    cause = pending_item_ids[ex_id]
                    pending_dependencies.append({
                        'target_type': 'verification_record',
                        'id': rid,
                        'name': f'验证记录：{rquestion}',
                        'impact_label': '待核对复测',
                        'reason': f'对应示例【{cause["name"]}】依赖待核对：{cause["reason"]}',
                    })
                else:
                    unaffected_items.append({
                        'target_type': 'verification_record',
                        'id': rid,
                        'name': f'验证记录：{rquestion}',
                    })
            elif rkind == 'relation':
                rel_id = rec.get('relation_id')
                if rel_id in diff['changed_relations']:
                    known_impacts.append({
                        'target_type': 'verification_record',
                        'id': rid,
                        'name': f'关联诊断：{rquestion}',
                        'impact': 'expired',
                        'impact_label': '诊断记录已过期',
                        'reason': '关联键、基数或关联配置已变更，需重新诊断。',
                    })
                else:
                    unaffected_items.append({
                        'target_type': 'verification_record',
                        'id': rid,
                        'name': f'关联诊断：{rquestion}',
                    })

    return {
        'summary': {
            'known_impacts_count': len(known_impacts),
            'pending_dependencies_count': len(pending_dependencies),
            'unaffected_count': len(unaffected_items),
            'revoked_sources_count': len(diff['revoked_sources']),
            'revoked_tables_count': sum(len(v) for v in diff['revoked_tables'].values()),
            'changed_columns_count': sum(len(v) for v in diff['changed_columns'].values()),
            'modified_metrics_count': len(diff['modified_metrics']) + len(diff['removed_metrics']),
            'modified_terms_count': len(diff['modified_terms']) + len(diff['removed_terms']),
            'modified_examples_count': len(diff['modified_examples']) + len(diff['removed_examples']),
        },
        'known_impacts': known_impacts,
        'pending_dependencies': pending_dependencies,
        'unaffected_items': unaffected_items,
    }
