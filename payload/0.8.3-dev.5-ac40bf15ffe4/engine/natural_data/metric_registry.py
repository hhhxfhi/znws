"""Trusted source adapters; source identity, coverage and metric semantics are separate.

Mappings are configured by the host, never accepted from model arguments. A source
without a reviewed mapping remains available to the read-only SQL capability.
"""
import re

LEGACY_SOURCE_MAPPINGS = {'intent-coverage-synthetic-v1': 'provincial-monthly/1'}
MAPPINGS = {'provincial-monthly/1': {
    'version': '2.0.0',
    'grain': {'certificate_monthly':['month','certificate_id'], 'profession_monthly':['month','certificate_id','profession','grade'], 'unit_inspection':['inspection_id'], 'basic_project_monthly':['month','project_id'], 'government_cases':['case_id'], 'map_reviews':['review_id'], 'tianditu_monthly':['month','platform_id','city']},
    'tables': ['certificate_monthly', 'profession_monthly', 'unit_inspection',
               'basic_project_monthly', 'government_cases', 'map_reviews', 'tianditu_monthly'],
}}
from .source_metrics import DEFINITION
MAPPINGS['district-national/1']=DEFINITION

SNAPSHOTS = {'certificate_monthly', 'profession_monthly', 'basic_project_monthly'}

def resolve_mapping(source):
    name = None if source.get('exclusive_business_calibers') else source.get('metric_mapping') or LEGACY_SOURCE_MAPPINGS.get(source['id'])
    if name not in MAPPINGS:
        raise ValueError('NOT_CONFIGURED: 当前来源未启用兼容指标映射。基础问数可使用data_sql_query（完整工具档为data_plan_query）；业务指标请在设置 → 智能问数 → 业务口径中配置并发布，再用data_semantic_plan。')
    return name, MAPPINGS[name]

def coverage_of(source):
    coverage = source.get('coverage', {})
    if not coverage and not source.get('exclusive_business_calibers') and source['id'] in LEGACY_SOURCE_MAPPINGS:
        # Read compatibility for the original frozen fixture, not a compiler rule.
        return {'years': [2023, 2024, 2025, 2026], 'complete_years': [2023, 2024, 2025],
                'first_month': '2023-01', 'latest_month': '2026-09'}
    return coverage

def validate_period(period, source, table=None):
    if not re.fullmatch(r'\d{4}(?:-(?:0[1-9]|1[0-2]))?', period):
        raise ValueError('INVALID_PERIOD: 使用 YYYY 或 YYYY-MM。')
    cov = coverage_of(source)
    cov = cov.get('per_table',{}).get(table,cov)
    if not cov.get('latest_month'):
        raise ValueError('UNKNOWN: 未登记来源的时间覆盖，不能推断完整年度。')
    if len(period) == 4:
        if int(period) not in cov.get('complete_years', []):
            raise ValueError('OUT_OF_COVERAGE: 该年度不在已确认的完整年份内。')
    elif period < cov.get('first_month', str(min(cov.get('years', [int(period[:4])])) )+'-01') or period > cov['latest_month']:
        raise ValueError('OUT_OF_COVERAGE: 统计月份不在已登记覆盖范围。')
    missing=cov.get('missing_periods', [])+cov.get('missing_periods_by_table',{}).get(table,[])
    if any(m==period or (len(period)==4 and m.startswith(period+'-')) for m in missing):
        raise ValueError('OUT_OF_COVERAGE: 此期间数据缺失，不能当零。')

def validate_mapping_fields(source, table, expressions):
    mapped = next((t for t in source['tables'] if t['name'] == table), None)
    if mapped is None:
        raise ValueError('SCHEMA_MISMATCH: 指标映射所需授权表缺失。')
    columns = {c['name'] for c in mapped['columns']}
    # Expressions are trusted registry SQL, not model-generated SQL.
    scrubbed = re.sub(r"'[^']*'", '', ' '.join(expressions))
    keywords = {'and', 'or', 'null', 'count', 'distinct', 'sum', 'nullif', 'avg'}
    fields = {x for x in re.findall(r'\b[a-z_][a-z_0-9]*\b', scrubbed.lower()) if x not in keywords}
    if not fields <= columns:
        raise ValueError('SCHEMA_MISMATCH: 指标映射字段缺失：' + ','.join(sorted(fields-columns)))
    return columns
