"""Typed capability boundary, not a second agent or workflow scheduler."""
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
import uuid
import threading
from types import UnionType
from typing import Literal, Union, get_args, get_origin
from pydantic import BaseModel, ValidationError
from .store import encode, ConflictError


_NAME = re.compile(r'[A-Za-z0-9_]+')


def _takes_text(field):
    annotation = getattr(field, 'annotation', None)
    return annotation is str or (get_origin(annotation) in (Union, UnionType) and str in get_args(annotation))


def _literal_strings(annotation):
    if get_origin(annotation) is Literal:
        return [x for x in get_args(annotation) if isinstance(x, str)]
    if get_origin(annotation) in (Union, UnionType):
        return [x for member in get_args(annotation) for x in _literal_strings(member)]
    return []


def _field_at(model, path):
    """Resolve nested schema paths for actionable validation feedback."""
    field = None
    for part in path:
        if isinstance(part, int):
            continue
        field = getattr(model, 'model_fields', {}).get(part)
        if field is None:
            return None
        members = [field.annotation]
        while members:
            annotation = members.pop()
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                model = annotation
                break
            members.extend(get_args(annotation))
        else:
            model = None
    return field


def repair_structured_values(value, annotation):
    """Decode JSON containers only where the contract requires a container.

    Local models sometimes JSON-encode nested tool objects a second time.
    This is lossless syntax recovery, not permission, value or scope inference.
    Plain text fields (including SQL) are never interpreted as JSON.
    """
    variants = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (annotation,)
    structured = [a for a in variants if get_origin(a) in (list, dict)
                  or isinstance(a, type) and issubclass(a, BaseModel)]
    if len(structured) != 1:
        return value
    kind = structured[0]
    is_list = get_origin(kind) is list
    if isinstance(value, str) and len(value) <= 100000:
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return value
        if isinstance(parsed, list if is_list else dict):
            value = parsed
    if is_list and isinstance(value, list):
        args = get_args(kind)
        return [repair_structured_values(v, args[0]) for v in value] if args else value
    if isinstance(kind, type) and issubclass(kind, BaseModel) and isinstance(value, dict):
        return {k:repair_structured_values(v, kind.model_fields[k].annotation) if k in kind.model_fields else v
                for k,v in value.items()}
    return value


def repair_argument_names(args, fields):
    """0.6.3: without thinking, small models append junk to a correct parameter name
    (group_byf, set_fields_param, source_idparam, baseline_fact_i[ref]) and then
    repeat the same call. Map such a name back to the one known parameter it starts
    with (or is a truncation of). Empty junk and exact duplicates are dropped; a
    conflicting value is left in place so validation still reports it."""
    if not isinstance(args, dict):
        return args
    known = list(fields)
    out = {k: v for k, v in args.items() if k in fields}
    for key, value in args.items():
        if key in fields:
            continue
        match = _NAME.match(str(key))
        stem = match.group(0) if match else ''
        found = [f for f in known if len(f) >= 3 and stem.startswith(f)]
        if not found and len(stem) >= 6:
            found = [f for f in known if f.startswith(stem)]
            if len(found) != 1:
                found = []
        targets = sorted(set(found), key=len, reverse=True)
        if not targets or (len(targets) > 1 and len(targets[0]) == len(targets[1])):
            out[key] = value
            continue
        target = targets[0]
        # Restore only an exact declared enum glued to its parameter name with
        # an empty placeholder. Conflicting values stay visible to validation;
        # this never invents an ID, permission or enum outside the typed schema.
        choices = _literal_strings(fields[target].annotation) if isinstance(fields, dict) else []
        glued = str(key)[len(target):].lstrip('_-:=')
        if choices and value in ('', None, 'null') and glued in choices:
            if target not in out:
                out[target] = glued
            elif out[target] != glued:
                out[key] = value
            continue
        if value in ('', None, [], {}):
            # The value was glued onto the name (source_idtest-database: ""): keep it
            # as the value when the field takes text and nothing else supplied it.
            glued = str(key)[len(target):].lstrip('_-:=')
            if glued and target not in out and _takes_text(fields[target]) if isinstance(fields, dict) else False:
                out[target] = glued
            continue
        if target in out:
            if out[target] == value or str(out[target]) == str(value):
                continue
            out[key] = value
            continue
        out[target] = value
    return out


@dataclass(frozen=True)
class Context:
    project: str
    request_key: str
    grants: frozenset[str]
    session_id: str = ""
    host_evidence: dict = field(default_factory=dict)
    authorization_scope: str = ""


@dataclass(frozen=True)
class Operation:
    name: str
    title: str
    description: str
    inputs: type[BaseModel]
    handler: object
    permission: str
    output_kind: str
    outputs: type[BaseModel]
    timeout_ms: int = 45000


@dataclass(frozen=True)
class Capability:
    id: str
    version: str
    title: str
    operations: tuple[Operation, ...]


class CapabilityRuntime:
    def __init__(self, store, policy=None):
        self.store = store
        self.policy = policy
        self.capabilities = {}
        self._scope_locks = {}
        self._locks_guard = threading.Lock()

    def register(self, capability):
        if capability.id in self.capabilities:
            raise ValueError('能力 ID 重复')
        if len({op.name for op in capability.operations}) != len(capability.operations):
            raise ValueError('工序名称重复')
        self.capabilities[capability.id] = capability

    def manifest(self, capability_id):
        cap = self.capabilities[capability_id]
        return {'contract_version': 1, 'id': cap.id, 'version': cap.version, 'title': cap.title,
                'operations': [{'name': op.name, 'title': op.title, 'description': op.description,
                                'input_schema': op.inputs.model_json_schema(), 'permission': op.permission,
                                'output_kind': op.output_kind, 'output_schema': op.outputs.model_json_schema(), 'timeout_ms': op.timeout_ms}
                               for op in cap.operations]}

    def recover(self):
        # One local worker owns this journal. Unknown calls are never auto-replayed.
        with self.store.connect() as c:
            c.execute("UPDATE capability_calls SET status='execution_unknown',finished_at=? WHERE status='running'", (time.time(),))

    def invoke(self, capability_id, operation, args, context):
        # A task mutation and its dependent execution cannot race in one session.
        with self._locks_guard:
            lock=self._scope_locks.setdefault((context.project,context.session_id),threading.RLock())
        with lock:
            return self._invoke(capability_id,operation,args,context)

    def _invoke(self, capability_id, operation, args, context):
        if self.policy: self.policy.require(context.project, capability_id, operation)
        cap = self.capabilities.get(capability_id)
        op = next((o for o in cap.operations if o.name == operation), None) if cap else None
        if op is None:
            raise ValueError('未知能力或工序')
        if op.permission not in context.grants:
            raise PermissionError('当前调用方没有此工序权限。')
        if not context.project or not context.request_key:
            raise ValueError('必须提供项目与调用标识')
        try:
            inputs = op.inputs.model_validate(repair_structured_values(repair_argument_names(args, op.inputs.model_fields), op.inputs))
        except ValidationError as error:
            # 0.6.2: name the wrong parameters and the allowed ones (names only, never
            # values) so a small model can fix the call instead of repeating it.
            errors = error.errors(include_input=False, include_context=False)
            declared = getattr(op.inputs, 'safe_validation_messages', {})
            if len(errors) == 1 and errors[0]['type'] in declared:
                # Only static, schema-owned guidance; never relay exception
                # context or the model's submitted values.
                raise ValueError(declared[errors[0]['type']]) from None
            unknown, missing, invalid = [], [], []
            for item in errors:
                name = '.'.join(str(part) for part in item.get('loc', ()) if not isinstance(part, int)) or '参数'
                kind = item.get('type', '')
                (unknown if kind == 'extra_forbidden' else missing if kind == 'missing' else invalid).append(name)
            parts = []
            if unknown: parts.append('不存在的参数：' + '、'.join(dict.fromkeys(unknown)))
            if missing: parts.append('缺少必填参数：' + '、'.join(dict.fromkeys(missing)))
            if invalid: parts.append('取值或类型不对：' + '、'.join(dict.fromkeys(invalid)))
            arrays = list(dict.fromkeys(str(item['loc'][0]) for item in errors
                                        if item.get('type') == 'list_type' and item.get('loc')))
            if arrays: parts.append('这些参数必须传 JSON 数组，不能传字符串：' + '、'.join(arrays))
            for name in dict.fromkeys(invalid + missing):
                field = _field_at(op.inputs, name.split('.'))
                choices = _literal_strings(field.annotation) if field else []
                if choices:
                    parts.append(name + ' 必须独立作为参数名，字符串取值只能是：' + json.dumps(choices, ensure_ascii=False))
                elif field and field.description:
                    # Schema-owned instructions only; never echo invalid values
                    # or exception contexts which might contain credentials.
                    parts.append(name + ' 填写要求：' + field.description[:500])
            for name in dict.fromkeys(x.split('.')[0] for x in unknown + missing + invalid if '.' in x):
                field = op.inputs.model_fields.get(name)
                if field and field.description:
                    parts.append(name + ' 对象结构：' + field.description[:500])
            allowed = '、'.join(op.inputs.model_fields)
            raise ValueError('ARGUMENT_SCHEMA: 工序参数不符合能力合约（' + '；'.join(parts) + '）。可用参数：' + allowed + '。请按这些参数名重新调用，不要重复同样的参数。') from None
        identity = {'version': cap.version, 'session': context.session_id,
                    'host_evidence': context.host_evidence, 'args': inputs.model_dump(mode='json')}
        if context.authorization_scope:
            identity['authorization_scope'] = context.authorization_scope
        digest = hashlib.sha256(encode(identity).encode()).hexdigest()
        qualified = cap.id + '.' + op.name
        step_id = uuid.uuid4().hex
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT id,digest,status,output FROM capability_calls WHERE project=? AND operation=? AND request_key=?', (context.project, qualified, context.request_key)).fetchone()
            if old:
                if old[1] != digest:
                    raise ConflictError('同一工序调用标识不能用于不同参数、版本或授权范围。请使用当前授权重新发起调用。')
                if old[3]:
                    return json.loads(old[3])
                return {'status': old[2], 'execution': {'id': old[0], 'operation': qualified, 'version': cap.version}, 'error': '该调用尚未确认完成，请检查执行记录，勿将其当作成功。'}
            c.execute('INSERT INTO capability_calls VALUES (?,?,?,?,?,?,?,?,?,?)', (step_id, context.project, context.request_key, qualified, cap.version, digest, 'running', None, time.time(), None))
        try:
            value = op.handler(inputs, context)
            try:
                value = op.outputs.model_validate(value).model_dump(mode='json', exclude_none=True)
            except ValidationError:
                raise TypeError('Capability output does not match its public contract') from None
            status = value.get('status', 'succeeded')
            if status not in {'succeeded', 'failed', 'cancelled', 'execution_unknown', 'running'}:
                raise ValueError('Invalid operation status')
            value = {**value, 'status': status, 'execution': {'id': step_id, 'operation': qualified, 'version': cap.version, 'contract_version': 1}}
            encoded = encode(value)
        except Exception as exc:
            with self.store.connect() as c:
                c.execute("UPDATE capability_calls SET status='failed',finished_at=? WHERE id=?", (time.time(), step_id))
            if isinstance(exc, (PermissionError, ValueError)):
                raise
            raise ValueError('能力执行失败；已保留失败状态，未自动重试。') from None
        with self.store.connect() as c:
            c.execute('UPDATE capability_calls SET status=?,output=?,finished_at=? WHERE id=?', (status, encoded, time.time(), step_id))
        return value
