"""Domain-only query engine. No agent, conversation or model orchestration."""
from __future__ import annotations

import copy
import csv
import math
from collections import OrderedDict
from decimal import Decimal
from datetime import date, datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from natural_analysis.claims import model_claims
from natural_work.store import ArtifactStore, ConflictError

from .vendor.plans import PolicyContext, validate_plan
from .read_limits import execution_basis
from .vendor.dialect import SqlDialect, _DIALECTS, get_dialect
from .vendor.charts import recommend_chart
from .vendor.features import compute_features, render_features_zh
from .vendor.metric_semantics import is_additive, number
from .vendor.operators import run_operators


class DuplicateResultColumns(ValueError):
    def __init__(self):
        super().__init__("DUPLICATE_RESULT_COLUMNS: 查询结果有同名列，无法区分两个值。请为每列指定不同别名后重新查询。")


class SQLiteDialect(SqlDialect):
    key = "sqlite"
    sqlglot = "sqlite"
    default_schema = "main"


_DIALECTS["sqlite"] = SQLiteDialect()


def _set_group_share_note(result):
    contract = result.get('answer_contract') or result.get('sql_answer_contract') or {}
    proof = contract.get('grouped_count_share')
    chart = result.get('chart')
    if not proof or not isinstance(chart, dict):
        return
    title = result.get('title') or ''
    scoped_title = re.sub(r'占全(?:省|市|区|县|国)(?:总[量数])?(?:的)?(?:比重|比例|占比|份额)', '本次查询占比', title)
    if scoped_title != title:
        result['title'] = scoped_title
        if chart.get('title') == title:
            chart['title'] = scoped_title
    share_col = proof['share_column']
    labels = result.setdefault('column_labels', {})
    if re.search(r'全省|全市|全区|全县|全国', labels.get(share_col, share_col)):
        labels[share_col] = '本次查询占比（%）'
        from .display_units import _fields
        for field in _fields(result):
            if field.get('field') == share_col:
                field['label'] = labels[share_col]
    disclosure = dict(chart.get('disclosure') or {})
    note = f"本次查询合计 {proof['denominator']:,} {proof['unit']}，占比按该查询范围计算。"
    if note not in (disclosure.get('note') or ''):
        disclosure['note'] = ' '.join(filter(None, [disclosure.get('note'), note]))
    chart['disclosure'] = disclosure


class DataEngine:
    _CHART_REVIEW_ALGO_VERSION = 1
    _CHART_REVIEW_CACHE_LIMIT = 512

    def __init__(self, home: Path, *, recover=True):
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = self.home / "executions.sqlite"
        self.config_path = self.home / "sources.json"
        self.lock = threading.RLock()
        self.active = {}
        self._config_cache = None
        self._chart_review_cache = OrderedDict()
        self.artifacts = ArtifactStore(self.home / "capabilities.sqlite")
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, request_key TEXT, digest TEXT, status TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(project,request_key))")
            if recover: c.execute("UPDATE runs SET status='execution_unknown' WHERE status='running'")

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def _config_identity(self):
        try:
            st = self.config_path.stat()
        except FileNotFoundError:
            return None
        return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)

    def _read_config(self):
        with self.lock:
            identity = self._config_identity()
            if self._config_cache and self._config_cache[0] == identity:
                return self._config_cache[1]
            if identity is None:
                cfg = {"sources": [], "projects": {}}
            else:
                cfg = json.loads(self.config_path.read_text())
            if identity == self._config_identity():
                self._config_cache = (identity, cfg)
            return cfg

    def config(self):
        return copy.deepcopy(self._read_config())

    def sources(self, project):
        if hasattr(self, 'business_projects'):
            return self.business_projects.sources(project)
        cfg = self._read_config()
        # Legacy standalone engine fixtures; production uses business-project grants.
        ids = cfg.get("projects", {}).get(project, [])
        return [s for s in cfg["sources"] if s["id"] in ids and s.get('connection_enabled',True)]

    def source(self, project, source_id):
        available = self.sources(project)
        for s in available:
            if s["id"] == source_id:
                return s
        choices = json.dumps([{'id':s['id'], 'name':s.get('name', s['id'])} for s in available[:16]], ensure_ascii=False)
        raise PermissionError('SOURCE_ID_INVALID: source_id 不在本项目当前可用目录中；这不代表数据库连接中断。'
                              '请从以下已授权来源复制准确 id 后重试，不要重复原参数，也不要自行猜测或截断 id：' + choices)

    def catalog(self, project, source_id=None):
        sources = self.sources(project)
        if source_id:
            sources = [self.source(project, source_id)]
        return {"sources": [{k: s[k] for k in ("id", "name", "dialect", "description", "tables", "terms", "examples", "synthetic", "coverage", "metric_mapping", "business_project", "business_skills", "business_layers", "business_dictionary", "connection_revision", "target_revision", "historical_qualification", "exclusive_business_calibers") if k in s} for s in sources], "project": project}

    def raw(self, project, rid):
        with self.connect() as c:
            row = c.execute("SELECT status,payload FROM runs WHERE id=? AND project=?", (rid, project)).fetchone()
        if not row:
            raise PermissionError("结果不存在或不属于此项目。")
        return {**json.loads(row[1]), "status": row[0], "result_ref": rid}

    def _dataset_ref_with_digest(self, project, result, *, connection=None):
        # Lazily adopt old results without rewriting their original query records.
        fields = ("result_ref", "title", "source_id", "columns", "rows", "row_count", "truncated", "column_labels", "column_unit_evidence", "quality", "null_reasons", "receipt", "sql", "question", "alert_rules", "plan_ref", "metric_contract", "facts", "source_provenance", "business_project", "inspection_reconciliation", "verified_claims", "validation", "request_scope", "result_lineage")
        payload = {k: result[k] for k in fields if k in result}
        return self.artifacts._put_with_digest(project, "data.dataset", payload, producer="data.query@1.0.0", ref="data:" + result["result_ref"],
                                               parents=[result["plan_ref"]] if result.get("plan_ref") else [], connection=connection)

    def dataset_ref(self, project, result, *, connection=None):
        ref, _ = self._dataset_ref_with_digest(project, result, connection=connection)
        return ref

    def get(self, project, rid, *, refresh_chart=True):
        result = self.raw(project, rid)
        authorized_sql = None
        restored_sql_contract = None
        monthly_window = (result.get('request_scope') or {}).get('monthly_sql_window')
        if getattr(self,'policy',None):self.policy.require_history(project)
        with self.artifacts.connect() as conn:
            if hasattr(self,'business_projects') and project.startswith('business/'):
                lineage = result.get('result_lineage') or {}
                visited = {rid}
                basis_result = result
                while lineage.get('kind') == 'derived_aggregation' and not basis_result.get('sql'):
                    parent = lineage.get('source_result_ref')
                    if not parent or parent in visited:
                        raise PermissionError('历史派生结果缺少可核对的原查询。')
                    visited.add(parent)
                    basis_result = self.raw(project, parent)
                    if basis_result.get('source_id') != result['source_id'] or basis_result.get('status') != 'succeeded':
                        raise PermissionError('历史派生结果的原查询与当前来源不一致。')
                    lineage = basis_result.get('result_lineage') or {}
                from .source_history import history_source
                source=history_source(self,self.business_projects.sources(project,include_disabled=True),result['source_id'])
                if source is None:raise PermissionError('历史结果来源已撤销授权')
                if basis_result.get('sql'):
                    plan = self.artifacts.get(project, basis_result['plan_ref'], connection=conn) if basis_result.get('plan_ref') else {}
                    query = plan.get('payload', {}).get('query', {})
                    parameters=query.get('parameters',[])
                    policy_sql=basis_result['sql']
                    # The executed row-limit rewrite is authorized through the
                    # validated SQL recorded with the same run, never by
                    # regenerating it (a parser upgrade may print it differently).
                    # Still check all other stored SQL directly, including damaged results.
                    basis=basis_result.get('execution_basis')
                    if basis and execution_basis(basis.get('policy_sql',''),policy_sql)==basis:
                        policy_sql=basis['policy_sql']
                    elif '__product_limit_rows' in policy_sql and query.get('logical_sql'):
                        from .read_limits import bounded_query
                        expected=bounded_query(query['logical_sql'],source['dialect'],query.get('limit',500),{p['name']:p['value'] for p in parameters})
                        if policy_sql==expected:policy_sql=query['logical_sql']
                    validate_plan({'mode':'sql','plan_id':'history-read','workspace_id':'history-read','datasource_ref':source['id'],'logical_sql':policy_sql,'parameters':parameters,'expected_chart':{'type':'table','title':'授权核查'},'limit':500},PolicyContext(workspace_id='history-read',allowed_datasource_refs=[source['id']],allowed_relations=[t['name'] for t in source['tables']],db_types={source['id']:source['dialect']}))
                    if not basis_result.get('metric_contract'):
                        from .query_evidence import require_result_evidence
                        from .vendor.dialect import sqlglot_name
                        require_result_evidence(policy_sql, sqlglot_name(source['dialect']))
                        if basis_result is result:
                            saved_project = result.get('business_project') or {}
                            live_project = source.get('business_project') or {}
                            same_publication = saved_project.get('version') is not None and all(
                                saved_project.get(k) == live_project.get(k) for k in ('id', 'version'))
                            if same_publication and ((result.get('sql_answer_contract') or {}).get('business_confirmation') == 'pending'):
                                from .sql_answer_contract import restore
                                restored_sql_contract = restore(result, source, plan, policy_sql)
                            if not result.get('column_unit_evidence') and (same_publication or 'column_unit_evidence' not in result):
                                authorized_sql = (policy_sql, source['dialect'], source if same_publication else None)
                            if (same_publication and monthly_window is None and result.get('status') == 'succeeded'
                                    and result.get('chart', {}).get('type') in ('column', 'bar', 'line', 'area', 'table')):
                                from .monthly_sql_scope import historical_display_window
                                monthly_window = historical_display_window(result, policy_sql, source)
            if result["status"] == "succeeded":
                columns = result.get("columns", [])
                if len(columns) != len(set(columns)):
                    # Older builds already overwrote one of the values. It cannot be recovered from this run.
                    raise DuplicateResultColumns()
                result["artifact_ref"], artifact_digest = self._dataset_ref_with_digest(project, result, connection=conn)
                view_revision, view = self.artifacts.head(project, rid, "data.view", connection=conn)
                result["view_revision"] = view_revision
                if view:
                    result.update(view["payload"])
                    result["view_ref"] = view["ref"]
                _, analysis = self.artifacts.head(project, rid, "data.analysis", connection=conn)
                if analysis:
                    for field in ("metrics", "operators", "analysis_scope"):
                        result.pop(field, None)
                    result.update(analysis["payload"])
                    result["analysis_ref"] = analysis["ref"]
                _, claims = self.artifacts.head(project, rid, "analysis.claims", connection=conn)
                if claims: result.update({k:v for k,v in claims["payload"].items() if k != "claims"})
                _, delivery = self.artifacts.head(project, rid, "data.delivery", connection=conn)
                if delivery: result['delivery']=delivery['payload']['delivery']
                from .display_units import apply_sql_units, normalize_units, sql_units
                if authorized_sql:
                    policy_sql, dialect, historical_source = authorized_sql
                    result['column_unit_evidence'] = sql_units(result, policy_sql, dialect, source=historical_source, historical=True)
                evidence = result.get('column_unit_evidence')
                if evidence is not None:
                    apply_sql_units(result, evidence)
                if isinstance(result.get("chart"), dict) and result["chart"]:
                    normalize_units(result)
                    if refresh_chart:
                        review_key = (
                            self._CHART_REVIEW_ALGO_VERSION,
                            artifact_digest,
                            view_revision,
                            result.get("view_ref", ""),
                            result.get("analysis_ref", ""),
                            json.dumps(result["chart"], sort_keys=True),
                            json.dumps(evidence, sort_keys=True) if evidence is not None else None,
                            result.get("reason_code"),
                            result.get("requested_chart"),
                        )
                        with self.lock:
                            cached_review = self._chart_review_cache.get(review_key)
                            if cached_review is not None:
                                self._chart_review_cache.move_to_end(review_key)
                        if cached_review is not None:
                            changed, cached_chart, cached_view, cached_viz = cached_review
                            result["chart"] = copy.deepcopy(cached_chart)
                            if cached_view is not None:
                                result["view"] = cached_view
                            if cached_viz is not None:
                                result["visualization"] = copy.deepcopy(cached_viz)
                            if changed and evidence is not None:
                                apply_sql_units(result, evidence)
                        else:
                            from .chart_selection import available_types, chart_spec_renderable, default_type, chart_for
                            kind = result["chart"].get("type", "table")
                            changed = False
                            viz_override = None
                            if kind != "table" and not chart_spec_renderable(result, result["chart"]):
                                spec = chart_for(result, kind)
                                if spec is None:
                                    spec = chart_for(result, default_type(result)) or chart_for(result, "table")
                                    viz_override = {
                                        "requested": result.get("requested_chart") or kind,
                                        "actual": spec["type"], "fallback": True,
                                        "reason": "原图类型不适用于当前结果，已切换为可用视图。",
                                    }
                                    result["visualization"] = viz_override
                                result["chart"], result["view"] = spec, spec["type"]
                                changed = True
                                normalize_units(result)
                                if evidence is not None:
                                    apply_sql_units(result, evidence)
                            alternatives = available_types(result)
                            result["chart"]["allowedAlternatives"] = alternatives
                            result["chart"]["recommendedType"] = default_type(result, honor_explicit=False, available=alternatives)
                            entry = (
                                changed,
                                copy.deepcopy(result["chart"]),
                                result.get("view") if changed else None,
                                copy.deepcopy(viz_override) if viz_override is not None else None,
                            )
                            with self.lock:
                                self._chart_review_cache[review_key] = entry
                                self._chart_review_cache.move_to_end(review_key)
                                while len(self._chart_review_cache) > self._CHART_REVIEW_CACHE_LIMIT:
                                    self._chart_review_cache.popitem(last=False)
                if evidence:
                    result['sql_column_units'] = {column: entry['unit'] for column, entry in evidence.items()}
            if not result.get('report'):
                revision,attached=self.artifacts.head(project,rid,'data.report-display', connection=conn)
                result['report_attachment_revision']=revision
                if attached:result['report_target']=attached['payload']['result_ref']
        if result.get('report'):
            from .reports import restore
            result=restore(self,project,result)
            result['artifact_ref']=result['report']['report_ref']
        from natural_analysis.delivery import compose
        # A composed contract needs a complete metric contract; an incomplete one
        # must not fabricate facts, so fall through to the SQL answer contract.
        mc = result.get("metric_contract")
        composed = compose(result) if isinstance(mc, dict) and all(k in mc for k in ("id", "unit", "periods")) else None
        result["answer_contract"] = (result.get("profession_ranking_contract") or composed
                                     or restored_sql_contract or result.get("sql_answer_contract"))
        from .answer_presentation import calculation_contract
        result['answer_contract'] = calculation_contract(result['answer_contract'])
        _set_group_share_note(result)
        if monthly_window and monthly_window.get('kind') != 'forecast_history':
            from .monthly_sql_scope import summarize
            if monthly := summarize(result, monthly_window):
                result['answer_contract'] = {**(result['answer_contract'] or {}),
                    'requested_months': monthly['requested_months'], 'monthly_sql_coverage': monthly}
        elif monthly_window:
            from .monthly_sql_scope import forecast_history_title
            title = forecast_history_title(result, monthly_window)
            if title != result['title']:
                result['title'] = title
                if result.get('chart'):
                    result['chart']['title'] = title
        for key in ('period_count_query_values', 'period_count_execution_basis', 'period_count_executed_sql',
                    'ranking_observation_execution_basis', 'ranking_observation_executed_sql'):
            result.pop(key, None)
        return result

    def save(self, project, rid, status, payload):
        with self.connect() as c:
            c.execute("UPDATE runs SET status=?,payload=? WHERE id=? AND project=?", (status, json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False), rid, project))

    def list_results(self, project):
        with self.connect() as c:
            rows = c.execute("SELECT id,status,payload FROM runs WHERE project=? ORDER BY rowid DESC LIMIT 60", (project,)).fetchall()
        return [{"result_ref": r[0], "status": r[1], "title": json.loads(r[2]).get("title", "查询结果")} for r in rows]

    def public(self, result):
        # Neither raw rows nor dimension values enter Harness tool transcripts.
        public = {k: result[k] for k in ("result_ref", "status", "title", "row_count", "truncated", "quality", "metrics", "receipt", "error", "visualization", "analysis_scope", "operators", "artifact_ref", "analysis_ref", "view_ref", "view_revision", "plan_ref", "facts", "metric_contract", "source_provenance", "business_project", "reason_code", "answer_contract", "repair", "report", "delivery", "validation") if k in result}
        public['validation'] = result.get('validation') or {'schema_version':0,'state':'unknown','label':'未执行本版检查'}
        if result.get('result_lineage'):public['result_lineage']=result['result_lineage']
        if result.get('verified_claims'):public['claims']=model_claims(result['verified_claims'][:30])
        if result.get('inspection_reconciliation'):public['inspection_reconciliation']=result['inspection_reconciliation']
        if result.get('status')=='succeeded':
            import math
            from .chart_selection import available_types
            alternatives = (result.get('chart') or {}).get('allowedAlternatives')
            public['visualization_options']={'available_types':alternatives if alternatives is not None else available_types(result),
                'instruction':'结果卡已自动呈现；仅用户要求换图时调用data_visualize，kind必须来自available_types。空值不能改成指标卡，保留表格及待复核原因。'}
            rows=result.get('rows',[])
            numeric=[c for c in result.get('columns',[]) if any(type(r.get(c)) in (int,float) for r in rows) and all(r.get(c) is None or type(r.get(c)) in (int,float) and math.isfinite(r[c]) for r in rows)]
            public['analysis_options']={'mode':'metric_facts' if result.get('facts') else 'field_statistics',
                'numeric_columns':numeric,'columns':result.get('columns',[]),
                'tools':['analysis_compute','analysis_method'] if result.get('facts') else ['analysis_field_statistics','analysis_method'],
                'complete':not result.get('truncated',False),'note':'使用结果列的单位依据。业务去重口径和可加性须另行核对。',
                # Receipts never carry row values. Say so, and how to read them,
                # so a model does not re-run the same query hoping to see numbers.
                'values_in_receipt':False,
                'read_values':('查询已成功，数值不在回执中（按设计只展示给用户）。不要重复执行同一查询。'+
                    ('单行结果：用 analysis_field_statistics 的 operation=max 逐列读取数值。' if len(rows)==1 else
                     '需要具体分组数值时，把所需分组改写为列（如 SUM(CASE WHEN 字段=值 THEN 1 ELSE 0 END)）并输出单行后读取；整体统计用 analysis_field_statistics。'))}
            if result.get('sql_column_units'):
                public['analysis_options']['column_units'] = result['sql_column_units']
        if public.get('report'):public['report']={k:v for k,v in public['report'].items() if k in ('report_ref','report_revision','spec','cell_count','title','columns')}
        if result.get('status')=='succeeded' and result.get('metric_contract',{}).get('detail'):
            rows=result.get('rows',[])
            public['detail_summary']={'fields':result.get('columns',[]),'row_count':len(rows),'complete':not result.get('truncated',False),'distinct_counts':{field:len({row[field] for row in rows if row.get(field) is not None}) for field in ['city','grade','profession'] if field in result.get('columns',[])},'scope':'当前返回结果；数据源覆盖范围不代表本名单覆盖范围。'}
        return public

    def query(self, project, args, request_key):
        from .repair import execute
        return execute(self,project,args,request_key)

    def _query_once(self, project, args, request_key):
        source = self.source(project, args["source_id"])
        if args.get('expected_catalog_source') and self.catalog(project,args['source_id'])['sources']!=args['expected_catalog_source']:raise ConflictError('STALE_SOURCE: 数据源配置已变化，需重新计划。')
        plan = {
            "mode": "sql", "plan_id": uuid.uuid4().hex, "workspace_id": hashlib.sha256(project.encode()).hexdigest()[:24],
            "datasource_ref": source["id"], "logical_sql": args["sql"], "parameters": args.get("parameters", []),
            "expected_chart": {"type": "table", "title": args.get("title", "查询结果")[:160]}, "limit": min(500, max(1, int(args.get("limit", 500))))
        }
        allowed = [t["name"] for t in source["tables"]]
        context = PolicyContext(workspace_id=plan["workspace_id"], allowed_datasource_refs=[source["id"]],
                                allowed_relations=allowed, db_types={source["id"]: source["dialect"]},
                                relation_schema=source.get('connection_schema') or 'public')
        validated = validate_plan(plan, context)
        if retry_ref := args.get('sql_timeout_retry_ref'):
            failed = self.raw(project, retry_ref)
            lineage = args.get('result_lineage') or {}
            if (failed['status'] != 'failed' or failed.get('reason_code') != 'TIMEOUT'
                    or lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
                    or not lineage.get('session_id') or lineage != failed.get('result_lineage')):
                raise ConflictError('STALE_QUERY_RETRY: 查询范围或数据配置已更新，请按当前范围重新计划。')
            earlier = self.artifacts.get(project, failed['plan_ref'])['payload']['query']
            if any(earlier[key] != validated.sanitized_plan[key]
                   for key in ('logical_sql', 'parameters', 'datasource_ref', 'limit')):
                raise ConflictError('STALE_QUERY_RETRY: 查询条件已更新，请按当前范围重新计划。')
        digest = hashlib.sha256(json.dumps({"source": source["id"], "args": args}, sort_keys=True).encode()).hexdigest()
        rid = uuid.uuid4().hex
        payload = {"title": plan["expected_chart"]["title"], "source_id": source["id"],
                   "source_provenance": {"name": source["name"], "origin": source.get("origin", ""), "synthetic": bool(source.get("synthetic", False))}}
        if args.get('validation'):
            payload['validation'] = args['validation']
            payload['request_scope'] = args.get('request_scope')
        if args.get('result_lineage'):
            payload['result_lineage'] = args['result_lineage']
        if source.get("business_project"):
            payload["business_project"] = source["business_project"]
        if 'connection_revision' in source:
            payload['source_provenance']['connection_revision'] = source['connection_revision']
        if args.get("metric_contract"):
            payload["metric_contract"] = args["metric_contract"]
        with self.lock, self.connect() as c:
            latest_source = self.source(project,source['id'])
            if latest_source.get('connection_revision',0)!=source.get('connection_revision',0):
                raise ConflictError('数据库连接已变化，请重新获取目录并计划。')
            if (latest_source.get('business_project') != source.get('business_project')
                    or latest_source['tables'] != source['tables']):
                raise ConflictError('STALE_SOURCE: 授权范围或业务口径已变化，请重新获取目录并计划。')
            if getattr(self,'policy',None):self.policy.require(project,'data','query')
            old = c.execute("SELECT id,digest FROM runs WHERE project=? AND request_key=?", (project, request_key)).fetchone()
            if old:
                if old[1] != digest:
                    raise ValueError("相同请求标识不能用于不同查询。")
                return self.public(self.get(project, old[0]))
            if len(self.active) >= 4:
                raise ValueError("查询队列繁忙，请稍后重试。")
            from .connections import connection_url
            url = connection_url(self, source)
            c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (rid, project, request_key, digest, "running", json.dumps(payload)))
            control = {"cancel": threading.Event(), "connection": None}
            self.active[(project, request_key)] = control
        start = time.monotonic()
        executing = sqlite_timed_out = False
        try:
            payload["plan_ref"] = self.artifacts.put(project, "data.plan", {"query": validated.sanitized_plan, "question": args.get("question", ""), "presentation_intent": args.get("chart_type")},
                producer="data.validate@1.0.0", ref="plan:" + rid,
                parents=([args["bound_plan_ref"]]+([args["repair_parent_ref"]] if args.get("repair_parent_ref") else [])) if args.get("bound_plan_ref") else [args["catalog_ref"]] if args.get("catalog_ref") else [])
            self.save(project, rid, "running", payload)
            params = {p["name"]: p["value"] for p in plan["parameters"]}
            from .read_limits import bounded_query
            logical_sql = validated.sanitized_plan['logical_sql']
            executed_sql = logical_sql
            period_count_plan = ranking_plan = None
            if not args.get('metric_contract') and any(word in logical_sql.casefold() for word in ('count', 'sum')):
                from .period_count_coverage import prepare as prepare_period_counts
                if prepared := prepare_period_counts(logical_sql, source, args.get('result_lineage'), args.get('request_scope')):
                    executed_sql, entries, scope = prepared
                    period_count_plan = (entries, scope)
                elif args.get('request_scope'):
                    from .ranking_observation import prepare as prepare_ranking
                    if ranking := prepare_ranking(logical_sql, source, args.get('result_lineage'), args['request_scope']):
                        executed_sql, ranking_plan = ranking
            if executed_sql != logical_sql:
                revised = validate_plan({**validated.sanitized_plan, 'logical_sql': executed_sql}, context)
                executed_sql = revised.sanitized_plan['logical_sql']
            sql = bounded_query(executed_sql, source['dialect'], plan['limit'], params)
            if source["dialect"] == "sqlite":
                path = Path(source["path"]).resolve()
                from .sqlite_connection import connect_readonly
                conn = connect_readonly(path)
                try:
                    conn.execute("PRAGMA query_only=ON")
                    # Defense in depth: runtime column/table authorizer, independent of AST.
                    relations = {x.split(".")[-1] for x in allowed}
                    # SQLite's JSON value expanders are eponymous virtual tables.
                    # Initialize only the validated built-ins before the authorizer
                    # (initialization reads sqlite_master). Never whitelist a real
                    # table/view with the same name or a custom virtual module.
                    from sqlglot import exp as sql_exp, parse_one
                    expansions = {t.this.name.lower() for t in parse_one(sql, read='sqlite').find_all(sql_exp.Table)
                                  if not t.db and not t.catalog and isinstance(t.this, sql_exp.Anonymous)
                                  and t.this.name.lower() in {'json_each', 'json_tree'}}
                    for expansion in expansions:
                        if conn.execute("SELECT 1 FROM sqlite_master WHERE lower(name)=?", (expansion,)).fetchone():
                            raise ValueError('SQL_VALUE_EXPANSION: 同名物理对象遮蔽了内置文本展开函数，请使用递归CTE拆分。')
                        conn.execute('SELECT value FROM '+expansion+"('[]') LIMIT 0").fetchall()
                    relations |= expansions
                    def authorize(action, table, column, database, trigger):
                        if action == sqlite3.SQLITE_READ and table not in relations:
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK
                    conn.set_authorizer(authorize)
                    deadline = start + 30

                    def stop_sqlite_query():
                        nonlocal sqlite_timed_out
                        if control['cancel'].is_set():
                            return 1
                        sqlite_timed_out = time.monotonic() > deadline
                        return int(sqlite_timed_out)

                    conn.set_progress_handler(stop_sqlite_query, 1000)
                    with self.lock:
                        control['connection'] = conn
                        if control['cancel'].is_set(): raise InterruptedError('查询已取消')
                    executing = True
                    cur = conn.execute(sql, params)
                    columns = [d[0] for d in cur.description]
                    values = cur.fetchmany(501)
                    executing = False
                finally:
                    conn.close()
            else:
                # Credentials never come from model arguments or project files.
                from .connections import read_connection
                if control['cancel'].is_set(): raise InterruptedError('查询已取消')
                with read_connection(source,url) as conn:
                    # Cancellation can happen during TCP/TLS connection setup.
                    # Register and check under the same lock used by cancel().
                    with self.lock:
                        control['connection'] = conn.connection.driver_connection
                        if control['cancel'].is_set(): raise InterruptedError('查询已取消')
                    executing = True
                    cur = conn.execute(text(sql), params)
                    columns, values = list(cur.keys()), cur.fetchmany(501)
                    executing = False
            if control["cancel"].is_set():
                raise InterruptedError("查询已取消")
            if len(columns) != len(set(columns)):
                raise DuplicateResultColumns()
            def json_value(value):
                if isinstance(value, Decimal): return float(value) if value.is_finite() else None
                if isinstance(value, (date, datetime)): return value.isoformat()
                if isinstance(value, float) and not math.isfinite(value): return None
                return value
            rows = [dict(zip(columns, map(json_value,row))) for row in values[:plan["limit"]]]
            execution_evidence = (args.get('validation') or {}).get('current_execution') or {}
            if execution_evidence.get('match') == 'validated_current_execution_sql' and rows:
                from .fiscal_history import PARTIAL_EXECUTION_COLUMNS
                unused = {column for column in PARTIAL_EXECUTION_COLUMNS if column in columns
                          and all(row[column] is None for row in rows)}
                if unused:
                    columns = [column for column in columns if column not in unused]
                    for row in rows:
                        for column in unused:
                            row.pop(column)
            period_count_proof = ranking_proof = None
            if period_count_plan:
                from .period_count_coverage import apply as apply_period_counts
                period_count_proof, original_zeros = apply_period_counts(rows, columns, period_count_plan, rid)
                if original_zeros:
                    payload['period_count_query_values'] = original_zeros
                # The public query receipt and lineage retain the input SQL.
                # Persist the actual DB statement separately for audit.
                payload['period_count_execution_basis'] = execution_basis(logical_sql, sql)
                payload['period_count_executed_sql'] = sql
                sql = bounded_query(logical_sql, source['dialect'], plan['limit'], params)
            if ranking_plan:
                from .ranking_observation import extract as extract_ranking
                ranking_proof = extract_ranking(rows, columns, ranking_plan, rid, complete=len(values)<=plan['limit'])
                payload['ranking_observation_execution_basis'] = execution_basis(logical_sql, sql)
                payload['ranking_observation_executed_sql'] = sql
                sql = bounded_query(logical_sql, source['dialect'], plan['limit'], params)
            # 0.6.2: semantic metrics return internal operand columns; keep them out of
            # the result and turn them into a reason for each empty value.
            null_reasons = [None] * len(rows)
            if args.get('metric_contract'):
                from .inspection_reconciliation import build as reconcile
                reconciliation=reconcile(rows,args['metric_contract'],source,rid)
                if reconciliation:
                    payload['inspection_reconciliation']={k:v for k,v in reconciliation.items() if k!='claims'}
                    payload['verified_claims']=reconciliation['claims']
            if any(str(c).startswith("__op_") for c in columns):
                from .result_checks import null_reasons as explain_nulls
                spec = (args.get("metric_contract") or {}).get("semantic_spec") or {}
                column_labels = {c["name"]: c.get("label") for t in source["tables"] for c in t["columns"]}
                rows, null_reasons = explain_nulls(rows, spec, column_labels)
                columns = [c for c in columns if not str(c).startswith("__op_")]
            if args.get("publish_facts") and "__quality_reason" in columns:
                columns.remove("__quality_reason")
                for i, row in enumerate(rows):
                    null_reasons[i] = row.pop("__quality_reason", None) or null_reasons[i]
            # Drivers may transport numeric aggregates as text. Normalize only
            # the declared metric operands, never identifiers/dimension labels.
            if args.get("metric_contract"):
                single_value = any(o.get('aggregation') == 'single_value' for o in (args['metric_contract'].get('semantic_spec') or {}).get('operands', []))
                for i, row in enumerate(rows):
                    if single_value and isinstance(row.get('value'), str):
                        from .value_types import _parse as parse_numeric_text
                        parsed = parse_numeric_text(row['value'])
                        row['value'] = parsed[0] if parsed and not parsed[1] else None
                    for field in ("value", "total", "cumulative"):
                        if field in row: row[field] = number(row[field])
                    if single_value and row.get('value') is None and not null_reasons[i]:
                        null_reasons[i] = '来源统计值为空或不是有限数值，不能按零处理；请核对原始记录。'
            # 0.6.3: some gateways (e.g. TBase) declare every column as text, so "51" and
            # "1,234.5" arrive as strings and the model keeps rewriting SQL to read them.
            # Restore reliably numeric columns of basic queries; codes with leading zeros,
            # long identifiers and period columns stay text (see value_types).
            from .value_types import numeric_text_columns
            text_numbers = {}
            if not args.get("metric_contract"):
                from .projection_types import projected_types
                rows, text_numbers = numeric_text_columns(columns, rows, source,
                    projected=projected_types(sql, source['dialect'], source))
            # Enforce a bounded retained/UI payload even for huge text cells.
            if len(json.dumps(rows, default=str).encode()) > 5 * 1024 * 1024:
                raise ValueError("结果超过 5 MB，请减少字段或聚合后查询。")
            labels = {c["name"]: c.get("label", c["name"]) for t in source["tables"] for c in t["columns"]}
            # 0.6.2: Chinese headers for English SQL aliases such as valid_cert_count.
            from .result_checks import alias_labels
            labels.update(alias_labels(columns, labels))
            for column, kind in text_numbers.items():
                if kind == "percent" and "%" not in str(labels.get(column, column)): labels[column] = str(labels.get(column, column)) + "（%）"
            if text_numbers: payload["text_numbers"] = text_numbers
            if payload.get("metric_contract"):
                from .result_lineage import presentation_title
                payload["title"] = presentation_title(payload["title"], {**payload, "rows": rows})
            payload.update(columns=columns, rows=rows, row_count=len(rows), truncated=len(values)>plan["limit"], column_labels=labels,
                chart=recommend_chart(columns, rows, expected_type=args.get("chart_type"), question=args.get("question", ""), title=payload["title"], labels=labels, alert_rules=args.get("alert_rules")),
                quality={"status": "available" if rows else "unavailable", "empty": not rows},
                receipt={"query_hash": validated.compiled_hash, "elapsed_ms": round((time.monotonic()-start)*1000), "source_id": source["id"], "read_only": True},
                sql=sql, question=args.get("question",""), requested_chart=args.get("chart_type"), alert_rules=args.get("alert_rules"))
            if sql != validated.sanitized_plan["logical_sql"]:
                payload["execution_basis"] = execution_basis(validated.sanitized_plan["logical_sql"], sql)
            if args.get("profession_ranking"):
                from .historical_qualification import ranking_contract
                payload['profession_ranking_contract']=ranking_contract(rid,payload,args['profession_ranking'])
                if payload['profession_ranking_contract']:
                    payload['column_labels']['单位数']='单位数（家）'
            if not args.get('metric_contract'):
                from .sql_answer_contract import budget_title, build as sql_answer_contract
                payload['sql_answer_contract'] = sql_answer_contract(rid, validated.sanitized_plan['logical_sql'],
                    source['dialect'], complete=not payload['truncated'], source=source,
                    parameters=validated.sanitized_plan['parameters'],
                    purpose=(payload.get('result_lineage') or {}).get('purpose') if period_count_plan is None else None,
                    validated_definition=((args.get('validation') or {}).get('current_execution')
                                          or (args.get('validation') or {}).get('published_report_budget')
                                          or (args.get('validation') or {}).get('published_report_spending')),
                    request_scope=args.get('request_scope'))
                if period_count_proof and payload['sql_answer_contract'] and not payload['truncated']:
                    payload['sql_answer_contract']['period_count_coverage'] = period_count_proof
                    if any(entry['state'] == 'unobserved' for entry in period_count_proof['columns']):
                        payload['sql_answer_contract']['limitations'].append(
                            '未查询到事实记录的期间保留为空；该期间的业务总数、占比和跨期变化无法核实。')
                if ranking_proof and payload['sql_answer_contract']:
                    payload['sql_answer_contract']['ranking_observation'] = ranking_proof
                cancellation = (payload['sql_answer_contract'] or {}).get('cancellation_object')
                if cancellation:
                    period = f'{cancellation["year"]} 年' if cancellation['year'] else ''
                    subject = '实际注销' if cancellation['mode'] == 'completed' else '申请注销案卷'
                    payload['title'] = period + subject + '专业分布'
                payload['title'] = budget_title(payload['title'], payload['sql_answer_contract'])
                for column, measure in ((payload['sql_answer_contract'] or {}).get('measurements') or {}).items():
                    if measure.get('role') == 'budget':
                        labels[column] = budget_title(labels.get(column, column), {'measurements': {column: measure}})
                if ((payload.get('request_scope') or {}).get('forecast_target') or {}).get('grain') == 'year':
                    from natural_analysis.forecasting import annual_amount_reference_label
                    if label := annual_amount_reference_label({**payload, 'result_ref': rid}, source):
                        column, measure = label
                        labels[column] = measure
                        payload['title'] = measure + '历史记录'
                payload['chart']['title'] = payload['title']
            if args.get("publish_facts"):
                payload["facts"] = [{"id": rid + ":" + str(i), "dimensions": {k:v for k,v in row.items() if k not in ("value","cumulative","total")},
                    "value": row.get("value"), **({"null_reason": null_reasons[i]} if null_reasons[i] else {}), "unit": args["metric_contract"]["unit"], "synthetic": bool(source.get("synthetic")), **({"denominator":row["total"]} if "total" in row else {}), **({"cumulative_percent":100*row["cumulative"]/row["total"] if row["total"] else None} if "cumulative" in row else {})} for i,row in enumerate(rows[:200])]
            if not args.get("metric_contract"):
                # 0.6.2: advisory checks for basic queries. Kept out of public(); only the
                # model receipt shows them (model_view.attach_values).
                from .result_checks import value_warnings, period_coverage
                payload["checks"] = {"value_warnings": value_warnings(columns, rows, labels),
                                     "period_coverage": period_coverage(columns, rows, labels)}
                if proof := (args.get('validation') or {}).get('published_grouping'):
                    from .row_count_aggregation import grouping_result
                    if grouped := grouping_result({**payload, 'result_ref': rid}, proof):
                        from .sql_answer_contract import configured_grouping
                        contract = configured_grouping(payload['sql_answer_contract'], grouped, source,
                                                       payload.get('result_lineage') or {})
                        payload['sql_answer_contract'] = {**contract, 'published_grouping': grouped}
            payload["reason_code"] = "NO_MATCH" if not rows else "ZERO_VALUE" if args.get("publish_facts") and all(r.get("value")==0 for r in rows) else "AVAILABLE"
            if args.get("publish_facts") and rows and any(r.get("value") is None for r in rows): payload["reason_code"] = "UNKNOWN"
            if any(null_reasons):
                payload["null_reasons"] = [{"dimensions": {k: v for k, v in row.items() if k not in ("value", "cumulative", "total")}, "reason": reason}
                                           for row, reason in zip(rows, null_reasons) if reason]
            payload["column_labels"].update({"period":"统计期间","value":payload.get("metric_contract",{}).get("id", "指标值"),"city":"地市","grade":"等级","profession":"专业","category":"审核类别","ownership":"单位性质","unit_name":"单位名称","project_name":"项目名称","reason":"案卷记载原因"})
            if args.get("metric_contract"):
                from .display_units import unit_label
                payload["column_labels"]["value"]=unit_label(payload["title"].replace("（合成测试）", ""),args["metric_contract"]["unit"])
            from .display_units import apply_sql_units, normalize_units, sql_units
            if not payload.get('metric_contract') and not payload.get('profession_ranking_contract'):
                from .model_view import derive_count_partition_shares
                evidence = sql_units(payload, validated.sanitized_plan['logical_sql'], source['dialect'], source=source)
                from .monthly_group_share import derive as monthly_group_shares
                if monthly_share := monthly_group_shares(payload, evidence, validated.sanitized_plan['logical_sql'], source):
                    percentages, proof = monthly_share
                    share_column = '当月占比'
                    columns.append(share_column)
                    labels[share_column] = share_column + '（%）'
                    for row, percentage in zip(rows, percentages):
                        row[share_column] = percentage
                    evidence[share_column] = {'unit': '%', 'kind': 'derived_percentage',
                                              'basis': '按同一月份完整分组的行数计算占比'}
                    payload['sql_answer_contract']['monthly_group_share'] = {
                        **proof, 'share_column': share_column,
                    }
                elif derived := derive_count_partition_shares(payload, evidence):
                    share_column, percentages, proof = derived
                    columns.append(share_column)
                    labels[share_column] = share_column + '（%）'
                    for row, percentage in zip(rows, percentages):
                        row[share_column] = percentage
                    evidence[share_column] = {'unit': '%', 'kind': 'derived_percentage',
                                              'basis': '按完整分组行数的合计计算占比'}
                    payload['sql_answer_contract']['grouped_count_share'] = proof
                    _set_group_share_note(payload)
                payload['column_unit_evidence'] = evidence
                apply_sql_units(payload, evidence)
            self.decorate_chart(payload)
            if 'column_unit_evidence' in payload:
                apply_sql_units(payload, payload['column_unit_evidence'])
            else:
                normalize_units(payload)
            if ((payload.get('sql_answer_contract') or {}).get('kind') == 'sql_aggregate'
                    and not payload['sql_answer_contract'].get('grouped_count_share')
                    and (payload.get('result_lineage') or {}).get('purpose') == 'answer'
                    and len(columns) == 3 and any(entry['unit'] == '%' for entry in payload.get('column_unit_evidence', {}).values())):
                from .model_view import _grouped_count_share, values_preview
                preview = values_preview({**payload, 'status': 'succeeded'})
                value = {'result_ref': rid, 'answer_contract': payload['sql_answer_contract'],
                         'analysis_options': {'column_units': {column: entry['unit']
                                                               for column, entry in payload['column_unit_evidence'].items()}}}
                if preview is not None and (share := _grouped_count_share(payload, value, preview, source,
                                                                            policy_sql=validated.sanitized_plan['logical_sql'])):
                    payload['sql_answer_contract']['grouped_count_share'] = share
                    _set_group_share_note(payload)
            self.save(project, rid, "succeeded", payload)
        except Exception as exc:
            # SQL errors can contain row values or connection credentials. Persist a safe diagnosis.
            from .query_errors import classify_query_failure
            native = getattr(exc, 'orig', None) or exc
            reason = classify_query_failure(native, dialect=source['dialect'], cancelled=control['cancel'].is_set(),
                                            executing=executing, sqlite_timed_out=sqlite_timed_out)
            payload['reason_code'] = reason
            status = 'cancelled' if reason == 'CANCELLED' else 'failed'
            payload["error"] = "查询已取消" if status == "cancelled" else f"查询失败（{type(exc).__name__}）。请检查字段、SQL 方言、数据源连接及结果大小。"
            if reason == 'TIMEOUT':
                payload["error"] = "查询未在限时内完成。请保留已核实的结果；如确需调整查询，沿用原统计范围和条件。"
            if reason != 'CANCELLED' and isinstance(exc, DuplicateResultColumns):
                payload.update(reason_code="DUPLICATE_RESULT_COLUMNS", error=str(exc))
            if reason != 'CANCELLED' and isinstance(exc, ValueError) and str(exc).startswith('ROW_LIMIT:'):
                payload.update(reason_code='ROW_LIMIT', error=str(exc))
            if reason == 'SCHEMA_MISMATCH':
                from .repair import diagnosis
                diagnostic = diagnosis(native, source)
                payload["error"] = "表或字段未匹配。请用 data_catalog(source_id=当前来源, table_name=准确表名) 查实际字段和类型，再修正 SQL。"
                if diagnostic and diagnostic.get("identifier"):
                    payload['repair_diagnostic'] = diagnostic
                    payload["error"] += " 未匹配的标识：" + diagnostic["identifier"]
            self.save(project, rid, status, payload)
        finally:
            with self.lock:
                self.active.pop((project, request_key), None)
        return self.public(self.get(project, rid))

    def cancel(self, project, request_key):
        with self.lock:
            state = self.active.get((project, request_key))
            if state is None:state=next((v for (p,k),v in self.active.items() if p==project and (k.startswith(request_key+':repair:') or k.startswith(request_key+':request:'))),None)
            if state:
                state["cancel"].set()
                conn = state["connection"]
                try:
                    if conn is not None:
                        (conn.interrupt if isinstance(conn, sqlite3.Connection) else conn.cancel)()
                except Exception:
                    pass
        return {"cancel_requested": state is not None}

    def analyze(self, project, rid, operators=None, metric_column=None):
        result = self.get(project, rid)
        if result["status"] != "succeeded":
            raise ValueError("仅能分析已成功的查询。")
        # Explicit numeric-column summaries; do not recurse through dimension values.
        result.pop("operators", None)
        metrics = {}
        for column in result["columns"]:
            raw = [r[column] for r in result["rows"] if r.get(column) is not None]
            if not raw or any(number(v) is None for v in raw):
                continue
            values = [number(v) for v in raw]
            calculated = run_operators({"compute_dispersion": True,"compute_quantiles": True,"metric_column": column}, result["columns"], result["rows"])
            metrics[column] = {"count":len(values), **({"sum":sum(values)} if is_additive(column) else {}), "mean":sum(values)/len(values), "min":min(values), "max":max(values), **{k:v for k,v in calculated.items() if isinstance(v,(int,float)) and not isinstance(v,bool)}}
        if operators:
            from .vendor.operators import OPERATORS
            if any(op not in OPERATORS for op in operators): raise ValueError("未知分析算子")
            if metric_column and metric_column not in result["columns"]: raise ValueError("未知分析字段")
            result["operators"] = run_operators({**{op:True for op in operators},**({"metric_column":metric_column} if metric_column else {})},result["columns"],result["rows"])
        result["analysis_scope"] = "仅当前返回结果行的统计；不可加总指标不提供合计，简单平均不代表总体加权均值。"
        result["metrics"] = metrics
        # Each analysis is immutable; publishing its head cannot overwrite a view.
        payload = {k: result[k] for k in ("metrics", "operators", "analysis_scope") if k in result}
        revision, _ = self.artifacts.head(project, rid, "data.analysis")
        self.artifacts.publish(project, rid, "data.analysis", payload, expected_revision=revision,
                               producer="data.analyze@1.0.0", parents=[result["artifact_ref"]])
        return self.public(self.get(project, rid))

    def visualize(self, project, rid, kind, expected_revision=None, *, view_patch=False):
        result = self.get(project, rid, refresh_chart=False)
        if result["status"] != "succeeded": raise ValueError("查询尚未成功。")
        from .chart_selection import chart_for, available_types, default_type
        from .display_units import apply_sql_units, normalize_units
        spec = chart_for(result, kind)
        alternatives = available_types(result)
        if spec is None:
            # Name the usable kinds so the next call is correct instead of
            # guessing again; the refusal itself is unchanged.
            raise ValueError("当前结果不适合该图形，原图已保留。可用图形："+"、".join(alternatives)+"；kind 请从中选择。")
        result["requested_chart"] = kind
        result["chart"] = spec
        result["chart"]["allowedAlternatives"] = alternatives
        result["chart"]["recommendedType"] = default_type(result, honor_explicit=False, available=alternatives)
        normalize_units(result)
        if 'column_unit_evidence' in result:
            apply_sql_units(result, result['column_unit_evidence'])
        _set_group_share_note(result)
        result["view"] = kind
        result["visualization"] = {"requested":kind,"actual":kind,"fallback":False,"reason":""}
        revision = result["view_revision"] if expected_revision is None else expected_revision
        payload = {k: result[k] for k in ("requested_chart", "chart", "view", "visualization")}
        ref, new_revision = self.artifacts.publish(project, rid, "data.view", payload, expected_revision=revision,
                               producer="data.visualize@1.0.0", parents=[result["artifact_ref"]])
        if view_patch:
            return {"view_patch": {"result_ref": rid, "base_view_revision": revision,
                    "view_ref": ref, "view_revision": new_revision, **payload}}
        result.update(view_ref=ref, view_revision=new_revision)
        return self.public(result)

    def decorate_chart(self, result):
        spec=result["chart"]
        from .chart_selection import chart_for
        from .chart_selection import default_type, _extract_explicit_chart
        import re as _re
        desired=result.get("requested_chart") or spec.get("type","table")
        # 0.6.2: one rule set (chart_selection.suitable/default_type). The model often
        # passes chart_type=table by habit, and the recommender's first pick may not be
        # buildable once a constant period column is dropped; both used to end in a
        # table. Keep a table only when the user asked for one in their own words; an
        # unsuitable or unbuildable choice falls back to the best suitable chart.
        is_detail=bool(result.get('metric_contract',{}).get('detail'))
        user_wants_table=is_detail or bool(_re.search(r"表格|明细|列表|清单|逐条",result.get("question","") or ""))
        # Only a chart the user named in their own words counts as requested; the
        # model's habitual chart_type is ignored otherwise.
        question = (result.get("question") or "").strip()
        auxiliary = ((result.get('validation') or {}).get('period_grain') or {}).get('state')=='auxiliary'
        explicit = None if auxiliary else _extract_explicit_chart(question)
        if auxiliary:
            result["requested_chart"] = None
        # Details are a default table preference, not consent for a
        # model-selected chart. A named suitable graph wins over that default.
        if explicit:
            result['requested_chart'] = desired = explicit
        elif result.get("requested_chart") and question and not (explicit or _re.search(r'(?<!地)图表', question)):
            result["requested_chart"]=None
        if is_detail and not result.get('requested_chart'):
            desired='table'
        elif not result.get("requested_chart"):
            # Nobody named a chart: the shared rules choose (the legacy recommender's
            # pick differed, e.g. horizontal bars for two categories).
            desired=default_type(result,honor_explicit=not auxiliary)
        elif desired=="table" and not user_wants_table:
            desired=default_type(result);result["requested_chart"]=None
        spec=chart_for(result,desired) or chart_for(result,default_type(result,honor_explicit=not auxiliary)) or chart_for(result,"table")
        requested=result.get("requested_chart")
        if requested=="table":spec={"type":"table","encoding":{},"allowedAlternatives":spec.get("allowedAlternatives",[]),"pivot":spec.get("pivot")}
        if requested=="alert-table" and result.get("alert_rules"):
            from .vendor.plans import AlertRule
            rules=[AlertRule.model_validate(rule).model_dump() for rule in result["alert_rules"]]
            if any(rule["column"] not in result["columns"] for rule in rules):raise ValueError("预警字段不在结果中")
            spec={"type":"alert-table","encoding":{},"alertRules":rules,"allowedAlternatives":["alert-table","table"]}
        result["chart"]=spec
        pivot=spec.get("pivot")
        if pivot and any(not is_additive(v) for v in pivot.get("values",[])):
            for total in (pivot.get("totals") or {}).values():
                total.update(showGrandTotals=False,showSubTotals=False)
            for col,agg in pivot.get("aggregations",{}).items():
                if not is_additive(col) and agg=="sum":pivot["aggregations"][col]="avg"
            pivot["note"]="不可加总指标已隐藏总计；单元格均值为简单平均，非总体加权值。"
        actual=spec.get("type","table")
        result["view"]=actual
        fallback=bool(requested and actual!=requested)
        reason=(spec.get("disclosure") or {}).get("note","")
        if fallback and requested in ('kpi','gauge') and len(result.get('rows',[]))!=1:
            label='指标卡' if requested=='kpi' else '仪表盘'
            reason=f'{label}需要单行结果；当前返回 {len(result.get("rows",[]))} 行，不能擅自合并为一个指标。'
        elif fallback and not reason:
            from .chart_selection import _shape
            shape=_shape(result)
            reason=('一次只能展示一个指标，本结果有 '+str(len(shape['measures']))+' 个指标' if requested in ('bar','column','pie','donut','treemap','funnel','waterfall','line','area') and len(shape['measures'])>1
                    else '数据的形态或单位不适合这种图')
        result["visualization"]={"requested":requested,"actual":actual,"fallback":fallback,"reason":reason}

    def test_source(self, sid):
        from .connections import read_connection, connection_url
        try:
            with self.lock:
                source=next((s for s in self.config()["sources"] if s["id"]==sid),None)
                if not source: raise ValueError("未知数据源")
                url = connection_url(self, source)
            with read_connection(source,url) as conn:conn.execute(text('SELECT 1')).fetchone()
        except Exception:raise ValueError('连接失败，请在数据库连接中检查配置、服务状态与账号权限。') from None
        return {"connected":True,"message":source["name"]+"连接成功。"}

    def export_csv(self, project, rid):
        result = self.get(project, rid)
        if result["status"] != "succeeded": raise ValueError("查询尚未成功。")
        output = io.StringIO(); writer = csv.writer(output)
        def cell(v):
            if v is None or isinstance(v, (int, float, Decimal)):
                return v
            s = str(v)
            return "'" + s if s.startswith(("\t", "\r", "\n")) or s.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")) else s
        writer.writerow([cell(name) for name in result["columns"]])
        writer.writerows([[cell(row.get(k)) for k in result["columns"]] for row in result["rows"]])
        self.artifacts.put(project, "data.export", {"result_ref": rid, "format": "csv", "created_at": time.time()},
                           producer="data.export@1.0.0", parents=[result["artifact_ref"]])
        return "\ufeff" + output.getvalue()
