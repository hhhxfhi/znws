"""Business ownership and published configuration, independent of user workspaces.

The host supplies the authenticated principal. A workspace is only an artifact
namespace, never a grant. JSON edits use revisions and share the engine lock.
"""
import copy
import hashlib
import json
import os
import getpass
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from natural_work.store import ConflictError

GROUPS = ('terms', 'examples', 'skills', 'layers')

def project_source(source, publication):
    if not publication or not publication.get('exclusive_business_calibers'):
        return source
    old_fields = {'metric_mapping', 'coverage', 'historical_qualification', 'adapter_review'}
    return {**{key: value for key, value in source.items() if key not in old_fields},
            'exclusive_business_calibers': True}

# Keep useful restore points; internal sequence numbers are not user-facing releases.
MAX_VERSIONS = 3


def configuration_changes(before, after):
    """Persist a small human-readable change list even after the older snapshot expires."""
    before = before or {}
    out = []
    for sid in sorted(set(before.get('source_ids', [])) | set(after.get('source_ids', []))):
        was, now = sid in before.get('source_ids', []), sid in after.get('source_ids', [])
        if was != now:
            out.append({'area': '数据范围', 'kind': '新增' if now else '移除', 'name': '数据源 ' + sid})
        elif now and sorted(before.get('tables', {}).get(sid, [])) != sorted(after.get('tables', {}).get(sid, [])):
            out.append({'area': '数据范围', 'kind': '修改', 'name': '授权表（' + sid + '）'})
    names = {'terms': '术语与指标', 'examples': '查询示例', 'skills': '分析方法说明', 'layers': '空间图层说明', 'mappings': '字段映射'}
    for group, area in names.items():
        old = {r['id']: r for r in before.get(group, [])}
        new = {r['id']: r for r in after.get(group, [])}
        for key in dict.fromkeys([*new, *old]):
            if key in old and key in new and old[key] == new[key]:
                continue
            row = new.get(key) or old[key]
            out.append({'area': area, 'kind': '删除' if key not in new else '新增' if key not in old else '修改',
                        'name': str(row.get('name') or row.get('question') or key)[:200]})
    return out


def retained_versions(project):
    versions = project.get('versions', [])
    ordered = sorted(versions, key=lambda v: v.get('version', 0))
    keep = ordered[-MAX_VERSIONS:]
    live = next((v for v in ordered if v.get('version') == project.get('published_version')), None)
    if live is not None and live not in keep:
        keep = [live] + ordered[-(MAX_VERSIONS - 1):]
    return keep


def trim_versions(cfg):
    for project in (cfg.get('projects') or {}).values():
        versions = project.get('versions')
        if not isinstance(versions, list) or len(versions) <= MAX_VERSIONS:
            continue
        project['versions'] = retained_versions(project)
    return cfg
ROLES = {'viewer': 0, 'editor': 1, 'admin': 2}

def local_principal():
    if os.environ.get('NATURAL_WORK_PRINCIPAL'):
        return os.environ['NATURAL_WORK_PRINCIPAL']
    if os.name == 'nt':
        return 'local:' + getpass.getuser()
    import pwd
    return 'local:' + pwd.getpwuid(os.getuid()).pw_name

def policy_key(scope):
    return '/'.join(scope.split('/')[:2]) if scope.startswith('business/') else scope


def normalize_workspace_path(workspace, *, require_abs=True):
    """Canonical workspace identity aligned with the host WorkspaceRegistry.

    The host canonicalizes every workspace path with fs.realpath, so the engine
    must use the same identity: realpath(normpath(text)) with POSIX repeated
    leading slashes collapsed. Write paths validate strictly (require_abs=True);
    read paths tolerate legacy non-canonical keys via _workspace_defaults.
    """
    if workspace is None or not isinstance(workspace, str):
        if require_abs:
            raise ValueError('请提供有效的工作区路径。')
        return ''
    text = workspace.strip()
    if not text or len(text) > 1024 or any(c in text for c in ('\x00', '\n', '\r')):
        if require_abs:
            raise ValueError('请提供有效的工作区路径。')
        return ''
    if text.startswith(('business/', 'personal/')):
        if require_abs:
            raise ValueError('工作区路径不能是内部作用域标识。')
        return ''
    if not os.path.isabs(text):
        if require_abs:
            raise ValueError('工作区路径必须是绝对路径。')
        return ''
    norm = os.path.normpath(text)
    # POSIX preserves exactly two leading slashes, but the host treats repeated
    # separators as the same workspace. Do not apply this to Windows UNC paths.
    if os.sep == '/' and norm.startswith('//'):
        norm = '/' + norm.lstrip('/')
    norm = os.path.realpath(norm)
    if require_abs and os.path.isfile(norm):
        raise ValueError('工作区路径必须是目录，不能是文件。')
    if len(norm) > 1024:
        if require_abs:
            raise ValueError('工作区路径过长。')
        return ''
    return norm


def _workspace_defaults(cfg, actor):
    """Read legacy non-canonical keys without choosing between conflicting grants.

    Legacy rows may carry malformed keys (e.g. embedded NUL) or lexical/symlink
    variants of the same host workspace. Reading drops malformed rows and
    collapses equivalent keys to the host canonical identity; two different
    grants for one canonical workspace are ambiguous and are both dropped
    rather than silently chosen. New writes still reject malformed input via
    normalize_workspace_path, and rewriting through this reader migrates the
    stored map away from malformed legacy keys.
    """
    values, conflicts = {}, set()
    for workspace, bid in (cfg.get('workspace_defaults', {}).get(actor, {}) or {}).items():
        try:
            key = normalize_workspace_path(workspace, require_abs=False)
        except ValueError:
            continue
        if not key:
            continue
        if key in values and values[key] != bid:
            conflicts.add(key)
        else:
            values[key] = bid
    for key in conflicts:
        values.pop(key, None)
    return values


class BusinessProjects:
    def __init__(self, engine):
        self.engine = engine
        self.path = engine.home / 'business-projects.json'
        self.lock = engine.lock
        self._snapshot_reads = ContextVar('business_project_reads', default=False)
        self._config_cache = None

    @contextmanager
    def read_snapshot(self):
        """Allow read-only access to the current file generation during this call."""
        token = self._snapshot_reads.set(True)
        try:
            yield
        finally:
            self._snapshot_reads.reset(token)

    def _file_identity(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)

    def _read_config(self):
        """Internal read-only access. Public config() always parses an independent copy."""
        return self.current_snapshot() if self._snapshot_reads.get() else self.config()

    def current_snapshot(self):
        """Share a read-only parsed generation. Check the file identity on every access."""
        with self.lock:
            for _ in range(3):
                identity = self._file_identity()
                if self._config_cache and self._config_cache[0] == identity:
                    return self._config_cache[1]
                self._config_cache = None
                cfg = self.config()
                current = self._file_identity()
                if current is not None and (identity is None or identity == current):
                    self._config_cache = (current, cfg)
                    return cfg
            raise ConflictError('业务配置正在更新，请重新读取。')

    def _write(self, cfg):
        # Finish immutable archival before removing anything from the active file.
        # An I/O failure leaves the previous active configuration intact.
        self._archive_expired(cfg)
        trim_versions(cfg)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)
        self._config_cache = None

    def _history_dir(self, bid):
        return self.engine.home / 'business-history' / hashlib.sha256(bid.encode()).hexdigest()

    def _archive_expired(self, cfg):
        for bid, project in cfg.get('projects', {}).items():
            keep = {v['version'] for v in retained_versions(project)}
            for entry in project.get('versions', []):
                if entry['version'] in keep:
                    continue
                payload = {'schema_version': 1, 'project_id': bid, 'version': entry}
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                digest = hashlib.sha256(raw).hexdigest()
                folder = self._history_dir(bid)
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = folder / f"v-{entry['version']}-{digest}.json"
                if path.exists():
                    if path.read_bytes() != raw:
                        raise OSError('配置历史归档校验失败，原配置未替换。')
                    continue
                temp = folder / ('.' + uuid.uuid4().hex + '.tmp')
                try:
                    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)

    def _archived_version(self, bid, version):
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return None
        paths = list(self._history_dir(bid).glob(f'v-{version}-*.json'))
        if len(paths) != 1:
            if paths:
                raise ValueError('历史版本存在冲突，请先核对归档。')
            return None
        path = paths[0]
        raw = path.read_bytes()
        if path.stem != f'v-{version}-' + hashlib.sha256(raw).hexdigest():
            raise ValueError('历史版本归档校验失败。')
        payload = json.loads(raw)
        if payload.get('project_id') != bid or payload.get('version', {}).get('version') != version:
            raise ValueError('历史版本归档身份不匹配。')
        return payload['version']

    def previous_version(self, project, version):
        """Find the nearest predecessor, including the compacted history."""
        active = max((entry for entry in project['versions'] if entry['version'] < version),
                     key=lambda entry: entry['version'], default=None)
        latest = active['version'] if active else 0
        for path in self._history_dir(project['id']).glob('v-*.json'):
            number = path.name.split('-', 2)[1]
            if number.isdecimal() and len(number) <= 20:
                candidate = int(number)
                if latest < candidate < version:
                    latest = candidate
        if active and active['version'] == latest:
            return active
        return self._archived_version(project['id'], latest) if latest else None

    def config(self):
        with self.lock:
            if self.path.exists():
                return json.loads(self.path.read_text())
            source_cfg = self.engine.config()
            actor = local_principal()
            cfg = {'schema_version': 1, 'revision': 0, 'admins': [actor], 'users': {actor: actor},
                   'projects': {}, 'defaults': {}, 'sessions': {}, 'scopes': {}}
            # Import data project identity, never folder identity or legacy user grants.
            for source in source_cfg.get('sources', []):
                identity = str(source.get('project_id') or source['id'])
                bid = 'p-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
                if bid not in cfg['projects']:
                    cfg['projects'][bid] = {'id': bid, 'name': source['name'], 'members': {actor: 'admin'},
                        'draft': {'source_ids': [], 'tables': {}, **{g: [] for g in GROUPS}},
                        'versions': [], 'published_version': 1}
                project = cfg['projects'][bid]
                project['draft']['source_ids'].append(source['id'])
                project['draft']['tables'][source['id']] = [t['name'] for t in source.get('tables', [])]
                for group in ('terms', 'examples'):
                    for item in source.get(group, []):
                        row = copy.deepcopy(item)
                        for k in (('name','definition') if group=='terms' else ('question','logical_sql')): row.setdefault(k,'')
                        if group == 'examples': row['source_id'] = source['id']
                        row['id'] = uuid.uuid4().hex
                        if not any(all(existing.get(k)==v for k,v in row.items() if k!='id') for existing in project['draft'][group]):
                            project['draft'][group].append(row)
            # Project-scoped imported skills/layers. Do not mix unmatched global rows.
            for project in cfg['projects'].values():
                sources = [s for s in source_cfg.get('sources', []) if s['id'] in project['draft']['source_ids']]
                project_ids = {str(s['project_id']) for s in sources if s.get('project_id') is not None}
                workspace_ids = {str(s['workspace_id']) for s in sources if s.get('workspace_id') is not None}
                imported_project = next((p for p in source_cfg.get('imported_settings',{}).get('product_project',[]) if str(p.get('project_id')) in project_ids),None)
                if imported_project: project['name'] = imported_project['name']
                for group, table in [('skills','product_analysis_skill'),('layers','product_gis_layer')]:
                    for row in source_cfg.get('imported_settings',{}).get(table,[]):
                        if str(row.get('project_id')) in project_ids or (row.get('project_id') is None and str(row.get('workspace_id')) in workspace_ids):
                            project['draft'][group].append({'id':uuid.uuid4().hex,'name':row.get('name') or row.get('skill_key') or row.get('layer_name') or '已导入配置','description':row.get('description') or '', 'config':row})
                project['versions'] = [{'version': 1, 'config': copy.deepcopy(project['draft']), 'actor': actor,
                                        'note': '从现有配置迁移', 'at': time.time()}]
            legacy_ids = next((ids for ids in source_cfg.get('projects', {}).values() if ids), [])
            default = next((p['id'] for p in cfg['projects'].values() if any(i in legacy_ids for i in p['draft']['source_ids'])), next(iter(cfg['projects']), ''))
            if default: cfg['defaults'][actor] = default
            self._write(cfg)
            return cfg

    def migrate_policy(self, policy):
        with self.lock, policy.lock:
            cfg = self.config(); settings = policy.config()
            if settings.get('business_migrated'): return
            legacy = self.engine.config().get('projects',{})
            ranks = {'enabled':0,'history':1,'disabled':2}
            for bid, project in cfg['projects'].items():
                folders = [folder for folder, ids in legacy.items() if set(ids).intersection(project['draft']['source_ids'])]
                local = {}
                skills = {}
                for folder in folders:
                    for cap,state in settings['projects'].get(folder,{}).items():
                        local[cap] = max([local.get(cap,'enabled'),state],key=ranks.get)
                    for skill,enabled in settings.get('skills',{}).get(folder,{}).items():
                        skills[skill] = skills.get(skill,True) and enabled
                if local: settings['projects']['business/'+bid] = local
                if skills: settings.setdefault('skills',{})['business/'+bid] = skills
            settings['business_migrated'] = True
            settings['revision'] += 1
            temp = policy.path.with_suffix('.tmp')
            temp.write_text(json.dumps(settings,ensure_ascii=False,indent=2)); temp.replace(policy.path)

    def require(self, cfg, actor, bid, role='viewer'):
        project = cfg['projects'].get(bid)
        if not project or ROLES.get(project['members'].get(actor), -1) < ROLES[role]:
            raise PermissionError('没有该业务项目的' + {'viewer':'咨询','editor':'编辑','admin':'管理'}[role] + '权限。')
        if project.get('archived') and role!='viewer': raise ValueError('业务项目已删除，请先恢复为草稿。')
        return project

    def snapshot(self, actor, *, summary=False, selected_project='', workspace='', inventory=None):
        cfg = self.current_snapshot() if summary else self.config()
        visible = [p for p in cfg['projects'].values() if actor in p['members'] and not p.get('archived')]
        selected = next((p['id'] for p in visible if p['id'] == selected_project), '')
        if not selected:
            preferred = _workspace_defaults(cfg, actor).get(normalize_workspace_path(workspace, require_abs=False)) or cfg['defaults'].get(actor)
            selected = next((p['id'] for p in visible if p['id'] == preferred), visible[0]['id'] if visible else '')
        if inventory is None:
            inventory = {s['id']: s for s in self.engine.config()['sources']}

        def scope_only(config):
            return {k: config.get(k, [] if k == 'source_ids' else {}) for k in ('source_ids', 'tables')}

        projects = []; archived = []
        for project in cfg['projects'].values():
            if actor not in project['members']: continue
            if project.get('archived'):
                if project['members'][actor]=='admin': archived.append({'id':project['id'],'name':project['name']})
                continue
            detailed = not summary or project['id'] == selected
            if not summary:
                value = copy.deepcopy(project)
            elif detailed:
                versions = retained_versions(project)
                # Legacy entries without a summary still need the full preceding config for their diff.
                if all(isinstance(v.get('changes'), list) for v in versions):
                    versions = [v if v['version'] == project['published_version']
                                else {**v, 'config': scope_only(v['config'])} for v in versions]
                value = copy.deepcopy({**project, 'versions': versions})
            else:
                value = copy.deepcopy({k: v for k, v in project.items() if k not in {'draft', 'versions'}})
                live = next((v for v in project['versions'] if v['version'] == project['published_version']), None)
                value['versions'] = copy.deepcopy([{**{k: v for k, v in live.items() if k not in {'config', 'changes', 'reviewed_builtin_packs'}},
                                                    'config': scope_only(live['config'])}]) if live else []
                value['draft'] = copy.deepcopy(scope_only(project.get('draft', {})))
                value['pending_change_count'] = len(configuration_changes(live['config'] if live else None, project.get('draft', {}))) if project['members'][actor] != 'viewer' else 0
            value['role'] = project['members'][actor]
            if summary:
                value['details_loaded'] = detailed
            if detailed:
                from .onboarding import project_readiness
                value['readiness'] = project_readiness(self.engine, project, inventory=inventory)
                if value['role']=='viewer' and not project['published_version']:
                    value['readiness']['sources']=[]
            if value['role'] == 'viewer':
                value.pop('draft', None); value.pop('members', None)
            if detailed and 'draft' in value:
                from .caliber_excel import excel_review_reason,legacy_candidate
                sources=list(inventory.values())
                value['review_candidates']={r['id']:v for r in value['draft']['terms'] if (v:=legacy_candidate(r,value['draft'],sources))}
                value['review_reasons'] = {r['id']:reason for r in value['draft']['terms'] if (reason := excel_review_reason(r))}
            projects.append(value)
        visible_ws_defaults = {
            ws: b for ws, b in _workspace_defaults(cfg, actor).items()
            if b in cfg['projects']
            and actor in cfg['projects'][b].get('members', {})
            and not cfg['projects'][b].get('archived')
            and cfg['projects'][b].get('published_version')
        }
        return {'revision': cfg['revision'], 'principal': actor, 'identity_kind': 'host',
                'system_admin': actor in cfg['admins'],
                'users': copy.deepcopy(cfg['users']) if actor in cfg['admins'] else {},
                'default_project': cfg['defaults'].get(actor, ''), 'workspace_defaults': visible_ws_defaults,
                'projects': projects, 'archived_projects':archived,
                **({'selected_project': selected} if summary else {})}

    def update(self, actor, action, args, *, pending_pack=None):
        with self.lock:
            cfg = self.config()
            if args.get('expected_revision') != cfg['revision']:
                raise ConflictError('项目配置已被修改，请刷新后重试。')
            bid = args.get('business_project', '')
            previous_live = cfg['projects'].get(bid, {}).get('published_version', 0)
            norm_ws = None
            if action == 'create':
                if actor not in cfg['admins']: raise PermissionError('只有系统管理员可以创建业务项目。')
                name = str(args.get('name','')).strip()
                if not name or len(name)>100: raise ValueError('请填写 1—100 字的项目名称。')
                bid = 'p-' + uuid.uuid4().hex[:16]
                cfg['projects'][bid] = {'id':bid,'name':name,'members':{actor:'admin'},'draft':{'source_ids':[],'tables':{},**{g:[] for g in GROUPS}},'versions':[],'published_version':0}
            elif action == 'restore':
                project=self.require(cfg,actor,bid)
                if project['members'][actor]!='admin': raise PermissionError('只有项目管理员可以恢复业务项目。')
                if not project.get('archived'): raise ValueError('业务项目未删除。')
                # Restore only a draft; never silently reinstate revoked data grants.
                ids={s['id'] for s in self.engine.config()['sources']}
                draft=project['draft']
                draft['source_ids']=[sid for sid in draft['source_ids'] if sid in ids]
                draft['tables']={sid:tables for sid,tables in draft['tables'].items() if sid in ids}
                draft['examples']=[r for r in draft['examples'] if r.get('source_id') in draft['source_ids']]
                draft['terms']=[r for r in draft['terms'] if (r.get('config', {}).get('source_id') or r.get('source_id')) in [None, '', *draft['source_ids']]]
                project.pop('archived',None);project['published_version']=0
            elif action == 'default':
                project = self.require(cfg, actor, bid)
                if project.get('archived'): raise ValueError('业务项目已删除。')
                if not project['published_version']: raise ValueError('项目发布后才能用于咨询。')
                cfg['defaults'][actor] = bid
            elif action == 'workspace_default':
                norm_ws = normalize_workspace_path(args.get('workspace'), require_abs=True)
                # Rewriting through the canonical reader collapses alias keys and
                # drops malformed legacy rows instead of carrying them forward.
                values = _workspace_defaults(cfg, actor)
                if not bid or args.get('clear') is True:
                    existing_bid = values.get(norm_ws)
                    if bid and existing_bid != bid:
                        self.require(cfg, actor, bid)
                    elif not existing_bid and actor not in cfg['admins'] and not any(actor in p.get('members', {}) and not p.get('archived') for p in cfg['projects'].values()):
                        raise PermissionError('没有修改工作区默认配置的权限。')
                    values.pop(norm_ws, None)
                    if values:
                        cfg.setdefault('workspace_defaults', {})[actor] = values
                    else:
                        cfg.get('workspace_defaults', {}).pop(actor, None)
                else:
                    project = self.require(cfg, actor, bid)
                    if project.get('archived'): raise ValueError('业务项目已删除。')
                    if not project['published_version']: raise ValueError('项目发布后才能用于咨询。')
                    values[norm_ws] = bid
                    cfg.setdefault('workspace_defaults', {})[actor] = values
            else:
                project = self.require(cfg, actor, bid, 'admin' if action in {'member','metadata','delete'} else 'editor')
                if action == 'delete':
                    if args.get('confirm_delete') is not True: raise ValueError('请确认删除业务项目。')
                    project['archived']=True
                    cfg['defaults']={user:default for user,default in cfg['defaults'].items() if default!=bid}
                    for user, ws_map in list(cfg.get('workspace_defaults', {}).items()):
                        cleaned = {ws: b for ws, b in ws_map.items() if b != bid}
                        if cleaned: cfg['workspace_defaults'][user] = cleaned
                        else: cfg['workspace_defaults'].pop(user, None)
                elif action == 'metadata':
                    name = str(args.get('name','')).strip()
                    description = str(args.get('description','')).strip()
                    if not name or len(name)>100 or len(description)>1000: raise ValueError('请填写有效的项目名称和说明。')
                    project['name'] = name; project['description'] = description
                elif action in {'draft','save','import_template'}:
                    current = next((v for v in project['versions'] if v['version'] == project['published_version']), None)
                    pending = current is not None and current['config'] != project['draft']
                    if action=='import_template':
                        from .onboarding import import_template
                        draft=import_template(self.engine,project,args.get('template'),args.get('source_bindings',{}))
                    else: draft = copy.deepcopy(args.get('draft'))
                    # An unrelated save must not approve metrics for a switched DB.
                    if isinstance(draft,dict):
                        from .caliber_excel import excel_review_reason, review_digest, reviewed, legacy_candidate
                        old_terms={r['id']:r for r in project['draft']['terms']}
                        inventory={s['id']:s for s in self.engine.config()['sources']}
                        for row in draft.get('terms',[]):
                            old = old_terms.get(row.get('id'),{})
                            spec=row.get('config',{})
                            if not isinstance(spec,dict): raise ValueError('指标配置必须是对象。')
                            confirm = row.pop('confirm_review',False) is True
                            row.pop('reviewed',None)
                            if spec.get('kind')=='semantic-metric' and spec!=old.get('config') and spec.get('source_id') in inventory:
                                spec['source_revision']=inventory[spec['source_id']].get('target_revision',0)
                            if old.get('origin') != 'excel': continue
                            pending_reason = excel_review_reason(old)
                            if confirm:
                                if action not in ('draft','save') or not pending_reason or spec.get('kind') != 'semantic-metric':
                                    raise ValueError('请选择待复核指标并补全结构化口径。')
                                candidate=legacy_candidate(old,project['draft'],inventory.values())
                                for f in old.get('review_filters',candidate.get('review_filters',[])):
                                    if not any(f in o.get('filters',[]) for o in spec.get('operands',[])):
                                        raise ValueError('请明确原始业务筛选适用的操作数后确认复核。')
                                row.update(origin='excel',source_id=spec.get('source_id'),reviewed={
                                    'by':actor,'at':int(time.time()),'digest':review_digest(spec)})
                                for k in ('status','review_reason','proposed_config','review_filters'): row.pop(k,None)
                            elif reviewed(old) and old.get('config') == spec:
                                row['reviewed'] = old['reviewed']
                            elif pending_reason or reviewed(old):
                                row.update(origin='excel',status='review',review_reason=pending_reason or '人工确认后的配置已改变，请重新复核')
                    self.validate(draft)
                    if draft['source_ids']!=project['draft']['source_ids'] or draft['tables']!=project['draft']['tables']:
                        self.require(cfg,actor,bid,'admin')
                        if set(draft['source_ids'])-set(project['draft']['source_ids']) and actor not in cfg['admins']:
                            raise PermissionError('新增数据源接入需由系统管理员配置。')
                    before_draft = project['draft']
                    project['draft'] = copy.deepcopy(draft)
                    if current and current['config'] == draft and not (pending_pack and pending_pack.get('replace_calibers') and not current.get('exclusive_business_calibers')):
                        project.pop('pending_builtin_packs', None)
                    else:
                        self._keep_pending_pack_rows(project)
                        if pending_pack:
                            self._record_pending_pack(project, before_draft, pending_pack)
                    if action == 'save' and not pending:
                        self.validate(project['draft'], publish=True, allow_empty=bool(project['versions']))
                        self._publish_snapshot(project, actor, '保存配置')
                elif action in {'publish', 'rollback'}:
                    if action == 'publish' and any(pack.get('replace_calibers') for pack in project.get('pending_builtin_packs', {}).values()):
                        self.require(cfg,actor,bid,'admin')
                    if action == 'rollback':
                        old = next((v for v in project['versions'] if v['version']==args.get('version')), None)
                        if old is None:
                            old = self._archived_version(bid, args.get('version'))
                        if not old: raise ValueError('发布版本不存在。')
                        if any(old['config'][field] != project['draft'][field] for field in ('source_ids','tables')):
                            self.require(cfg,actor,bid,'admin')
                        project['draft'] = copy.deepcopy(old['config'])
                        project.pop('pending_builtin_packs', None)
                    self.validate(project['draft'], publish=True, allow_empty=bool(project['versions']))
                    self._publish_snapshot(project, actor, args.get('note') or ('恢复历史配置' if action=='rollback' else '保存配置'), old if action=='rollback' else None)
                elif action == 'member':
                    user, role = str(args.get('user','')).strip(), args.get('role')
                    if not user or len(user)>200 or role not in {*ROLES, 'remove'}: raise ValueError('请填写可信身份标识和有效角色。')
                    members = dict(project['members'])
                    if role == 'remove':
                        members.pop(user,None)
                        if cfg['defaults'].get(user) == bid:
                            cfg['defaults'].pop(user, None)
                        if user in cfg.get('workspace_defaults', {}):
                            cleaned = {ws: b for ws, b in cfg['workspace_defaults'][user].items() if b != bid}
                            if cleaned: cfg['workspace_defaults'][user] = cleaned
                            else: cfg['workspace_defaults'].pop(user, None)
                    else: members[user] = role
                    if 'admin' not in members.values(): raise ValueError('项目必须保留至少一名管理员。')
                    project['members'] = members
                    cfg['users'].setdefault(user, user)
                else: raise ValueError('未知项目操作。')
            cfg['revision'] += 1
            self._write(cfg)
            result = self.snapshot(actor, summary=args.get('response_view') == 'manager', selected_project=bid)
            if action in ('save', 'publish', 'rollback'):
                result['published_changed'] = cfg['projects'][bid]['published_version'] != previous_live
            if action == 'workspace_default' and norm_ws:
                result['normalized_workspace'] = norm_ws
            result['selected_project'] = bid
            return result

    @staticmethod
    def _pack_row_refs(draft, pack_id, version, source_id, before):
        origin = f'pack:{pack_id}@{version}'
        mapping_prefix = 'pack-' + re.sub(r'[^a-zA-Z0-9-]', '', pack_id)[:40] + '-m-'
        old_mappings = {row['id'] for row in before.get('mappings', [])}
        refs = []
        for group in ('terms', 'examples', 'mappings'):
            for row in draft.get(group, []):
                if row.get('source_id') != source_id:
                    continue
                if row.get('origin') == origin or (group == 'mappings' and row['id'].startswith(mapping_prefix)
                                                    and row['id'] not in old_mappings):
                    refs.append([group, row['id']])
        return refs

    @classmethod
    def _record_pending_pack(cls, project, before, pack):
        refs = cls._pack_row_refs(project['draft'], pack['id'], pack['version'], pack['source_id'], before)
        if refs:
            project.setdefault('pending_builtin_packs', {})[pack['id']] = {
                'version': pack['version'], 'source_id': pack['source_id'], 'actor': pack['actor'],
                'note': str(pack['note'])[:500], 'state': 'pending_review', 'at': time.time(), 'rows': refs,
                **({'replace_calibers': True} if pack.get('replace_calibers') else {}),
            }

    @staticmethod
    def _keep_pending_pack_rows(project):
        pending = project.get('pending_builtin_packs', {})
        if not pending:
            return
        draft = project['draft']
        actual = {(group, row['id'], row.get('source_id')) for group in ('terms', 'examples', 'mappings')
                  for row in draft.get(group, [])}
        for pack_id, entry in list(pending.items()):
            if not any((group, row_id, entry.get('source_id')) in actual for group, row_id in entry.get('rows', [])):
                pending.pop(pack_id)
        if not pending:
            project.pop('pending_builtin_packs', None)

    @staticmethod
    def _publish_snapshot(project, actor, note, restored=None):
        BusinessProjects._keep_pending_pack_rows(project)
        current = next((v for v in project['versions'] if v['version'] == project['published_version']), None)
        pending = project.get('pending_builtin_packs', {})
        if current and current['config'] == project['draft'] and not pending:
            return
        version = max([v['version'] for v in project['versions']], default=0) + 1
        entry = {'version': version, 'config': copy.deepcopy(project['draft']), 'actor': actor,
                 'note': str(note)[:500], 'at': time.time(),
                 'changes': configuration_changes(current['config'] if current else None, project['draft'])}
        if (restored or current or {}).get('exclusive_business_calibers'):
            entry['exclusive_business_calibers'] = True
        if restored:
            entry.update(restored_from=restored['version'], restored_from_at=restored.get('at'))
        project['versions'].append(entry)
        project['published_version'] = version
        if pending:
            if any(value.get('replace_calibers') for value in pending.values()):
                project['builtin_packs'] = {}
                entry['exclusive_business_calibers'] = True
            project.setdefault('builtin_packs', {}).update({key: value['version'] for key, value in pending.items()})
            entry['reviewed_builtin_packs'] = copy.deepcopy(pending)
            project.pop('pending_builtin_packs', None)

    @staticmethod
    def _rebase_builtin_draft(draft, live, next_live, pending, source_id):
        """Apply the published pack delta around the administrator's pending edits."""
        from .presets import _digest

        if draft == live:
            return copy.deepcopy(next_live)
        rebased = copy.deepcopy(draft)
        if set(draft['tables'].get(source_id, [])) != set(live['tables'][source_id]):
            return rebased
        system_rows = {tuple(ref) for ref in pending.get('rows', [])}
        for group in ('terms', 'examples', 'mappings'):
            original = {row['id']: row for row in live.get(group, [])}
            published = {row['id']: row for row in next_live.get(group, [])}
            added = [row for row_id, row in published.items() if row_id not in original]
            kept = {}
            for row in rebased.get(group, []):
                row_id = row['id']
                pristine_staged = (group, row_id) in system_rows and row.get('origin_digest') == _digest(row)
                if row == original.get(row_id) or pristine_staged:
                    if row_id in published:
                        kept[row_id] = published[row_id]
                else:
                    kept[row_id] = row
            for row in added:
                if (group, row['id']) not in system_rows:
                    kept.setdefault(row['id'], row)
            if kept or group in rebased:
                rebased[group] = list(kept.values())
        return rebased

    def prepare_builtin_pack(self, project, draft, pack_id, version, note, *, source_id, activate):
        """Build a new project snapshot off the query lock."""
        current = next((v for v in project['versions'] if v['version'] == project['published_version']), None)
        if not current:
            return None
        project = copy.deepcopy(project)
        draft = copy.deepcopy(draft)
        live = current['config']
        record = {'id': pack_id, 'version': version, 'source_id': source_id,
                  'actor': 'system:builtin-pack', 'note': note}
        if not activate:
            if live != project['draft']:
                raise ConflictError('存在未生效修改，不能自动导入口径草稿。')
            self.validate(draft, publish=False, allow_empty=True)
            if draft == live:
                return None
            project['draft'] = draft
            self._record_pending_pack(project, live, record)
        else:
            if any(draft[key] != live[key] for key in ('source_ids', 'tables')):
                raise ConflictError('口径绑定期间授权数据目录已改变。')
            self.validate(draft, publish=True)
            remaining = project.pop('pending_builtin_packs', {})
            previous = remaining.get(pack_id, {})
            if previous.get('actor') == record['actor'] and previous.get('source_id') == source_id:
                remaining.pop(pack_id)
            else:
                previous = {}
            editor_draft = self._rebase_builtin_draft(project['draft'], live, draft, previous, source_id)
            project['draft'] = draft
            self._publish_snapshot(project, record['actor'], note)
            project.setdefault('builtin_packs', {})[pack_id] = version
            project['draft'] = editor_draft
            if remaining:
                project['pending_builtin_packs'] = remaining
                self._keep_pending_pack_rows(project)
        return project

    def commit_builtin_packs(self, updates, source_identity):
        """Publish compatible updates atomically and preserve concurrent user changes."""
        if not updates:
            return set()
        with self.lock:
            if self.engine._config_identity() != source_identity:
                raise ConflictError('口径绑定期间授权数据目录已改变。')
            cfg = self.config()
            applied = set()
            for bid, (original, staged) in updates.items():
                if cfg['projects'].get(bid) == original:
                    cfg['projects'][bid] = staged
                    applied.add(bid)
            if applied:
                cfg['revision'] += len(applied)
                self._write(cfg)
            return applied

    def validate(self, draft, publish=False, allow_empty=False):
        if not isinstance(draft,dict) or set(draft)-{'source_ids','tables','mappings',*GROUPS} or not {'source_ids','tables',*GROUPS}<=set(draft): raise ValueError('业务配置结构无效。')
        sources = {s['id']:s for s in self.engine.config().get('sources',[])}
        ids = draft['source_ids']
        if not isinstance(ids,list) or any(not isinstance(i,str) or i not in sources for i in ids) or len(set(ids))!=len(ids): raise ValueError('数据源不存在或重复。')
        if publish and not ids and not allow_empty: raise ValueError('请至少接入一个数据源后发布。')
        if not isinstance(draft['tables'],dict) or set(draft['tables'])!=set(ids): raise ValueError('请为每个数据源设置明确的授权表。')
        for sid,names in draft['tables'].items():
            if not isinstance(names,list) or any(n not in {t['name'] for t in sources[sid]['tables']} for n in names) or len(set(names))!=len(names): raise ValueError('授权表不存在或重复。')
            if publish and not names: raise ValueError('已接入的数据源至少需要一张授权表。')
        from .business_dictionary import validate_mappings
        if 'mappings' in draft: draft['mappings']=validate_mappings(draft['mappings'], sources, draft['tables'])
        metric_keys=set()
        limited_metric_keys=set()
        for group in GROUPS:
            rows = draft[group]
            if not isinstance(rows,list) or len(rows)>1000: raise ValueError('配置条目过多。')
            seen = set()
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row['id'] in seen: raise ValueError('配置条目标识缺失或重复。')
                seen.add(row['id'])
                if 'config_draft' in row:raise ValueError('结构化配置尚未完成，请补全JSON后保存。')
                if 'config' in row and not isinstance(row['config'],dict):raise ValueError('结构化配置必须是JSON对象。')
                fields = ('name','definition') if group=='terms' else ('question','logical_sql') if group=='examples' else ('name','description')
                if any(not isinstance(row.get(k),str) or len(row[k])>10000 or not row[k].strip() for k in fields): raise ValueError('请补全配置名称及内容。')
                if group=='terms' and row.get('source_id') and row['source_id'] not in ids:
                    raise ValueError('口径说明的适用数据库不在本项目中。')
                if group=='terms' and 'clarification' in row:
                    from .business_ambiguity import validate_rule
                    validate_rule(row['clarification'])
                if group=='terms' and row.get('config',{}).get('kind')=='metric-limitation':
                    from .metric_limitations import validate
                    validate(row, ids)
                    key = (row['config']['source_id'], row['config']['id'].casefold())
                    if key in limited_metric_keys:
                        raise ValueError('METRIC_LIMITATION_CONFIG: 同一来源的不可计算指标标识重复。')
                    limited_metric_keys.add(key)
                if group=='terms' and row.get('config',{}).get('kind')=='row-count-aggregation':
                    from .row_count_aggregation import validate
                    sid = row['config'].get('source_id')
                    if not isinstance(sid, str) or sid not in ids or row.get('source_id', sid) != sid:
                        raise ValueError('DERIVE_COUNT_CONFIG: 逐行计数依据必须属于同一授权数据源。')
                    source = {**sources[sid], 'tables': [t for t in sources[sid]['tables'] if t['name'] in draft['tables'][sid]]}
                    validate(row['config'], source)
                if group=='terms' and row.get('config',{}).get('kind')=='semantic-metric':
                    from .semantic import validate
                    sid=row['config'].get('source_id')
                    if sid not in ids:raise ValueError('复杂指标来源未授权')
                    key=(sid,row['config'].get('id'),row['config'].get('version'))
                    if key in metric_keys:raise ValueError('同一来源的指标标识和版本重复，请编辑已有指标或增加版本。')
                    metric_keys.add(key)
                    source={**sources[sid],'tables':[t for t in sources[sid]['tables'] if t['name'] in draft['tables'][sid]]}
                    validate(row['config'],source)
                if group=='skills' and row.get('config',{}).get('kind')=='analysis-skill':
                    from natural_analysis.methods import validate_custom
                    validate_custom(row['config'])
                if group=='examples':
                    if row.get('source_id') not in ids: raise ValueError('查询示例必须属于本项目的数据源。')
                    if publish:
                        from .vendor.plans import validate_plan, PolicyContext
                        source=sources[row['source_id']]
                        plan={'mode':'sql','plan_id':uuid.uuid4().hex,'workspace_id':'publish','datasource_ref':source['id'],'logical_sql':row['logical_sql'],'parameters':[],'expected_chart':{'type':'table','title':'校验'},'limit':1}
                        validate_plan(plan,PolicyContext(workspace_id='publish',allowed_datasource_refs=ids,allowed_relations=draft['tables'][source['id']],db_types={source['id']:source['dialect']}))

        if publish:
            from .definition_evidence import scoped_terms
            from .business_ambiguity import reference_issues
            for sid in ids:
                source={**sources[sid],'tables':[t for t in sources[sid]['tables'] if t['name'] in draft['tables'][sid]],'terms':scoped_terms(draft,sid,sources)}
                errors=reference_issues(source)
                if errors:raise ValueError('CONFIGURATION_REFERENCE_GAP: '+errors[0]['name']+'：'+errors[0]['message']+' '+','.join(errors[0]['metric_ids']))

    def _session_info(self, cfg, actor, session, workspace, selected=None):
        if not session or len(session)>256: raise ValueError('缺少有效的会话标识。')
        old = cfg['sessions'].get(actor + ':' + session)
        # A workspace preference selects only an already authorized project.
        preferred = _workspace_defaults(cfg, actor).get(workspace) if workspace else None
        preferred_project = cfg['projects'].get(preferred, {})
        if (actor not in preferred_project.get('members', {}) or preferred_project.get('archived')
                or not preferred_project.get('published_version')):
            preferred = None
        bid = selected or (old or {}).get('business_project') or preferred or cfg['defaults'].get(actor, '')
        if not bid:
            candidates = [p['id'] for p in cfg['projects'].values() if actor in p['members']
                          and not p.get('archived') and p['published_version']]
            if len(candidates) == 1:
                bid = candidates[0]
        if not bid:
            return {'business_project': '', 'locked': False}
        project = self.require(cfg, actor, bid)
        if project.get('archived'):
            raise PermissionError('该会话的业务项目已删除，请在设置中选择默认项目并新建会话。')
        if selected:
            if old and old.get('locked') and old['business_project'] != bid:
                raise ConflictError('会话已开始执行，请新建会话切换业务项目，避免混用数据和口径。')
            if not project['published_version']:
                raise ValueError('业务项目尚未发布。')
        return {'business_project': bid, 'name': project['name'], 'locked': bool((old or {}).get('locked')),
                'published_version': project['published_version']}

    def session(self, actor, session, selected=None, workspace=''):
        workspace = normalize_workspace_path(workspace, require_abs=False)
        with self.lock:
            cfg = self.config() if selected else self._read_config()
            info = self._session_info(cfg, actor, session, workspace, selected)
            if selected:
                key = actor + ':' + session
                cfg['sessions'][key] = {**cfg['sessions'].get(key, {}), 'business_project': info['business_project']}
                self._write(cfg)
            return info

    def context_scope(self, actor, workspace, session='', required=True):
        if required or workspace.startswith('business/'):
            return self.resolve(actor,workspace,session)
        workspace = normalize_workspace_path(workspace, require_abs=False)
        selected = self.session(actor, session or '__workspace_preview__', workspace=workspace)
        if selected['business_project'] and selected['published_version']:
            return self.resolve(actor,workspace,session)
        # General Harness tasks and knowledge do not require a data authorization.
        return 'personal/'+hashlib.sha256((actor+'\0'+workspace).encode()).hexdigest()[:24]

    def resolve(self, actor, workspace, session=''):
        if workspace.startswith('business/'):
            with self.lock:
                cfg = self._read_config()
                scope = cfg['scopes'].get(workspace)
                if not scope or scope['actor']!=actor: raise PermissionError('结果不属于当前用户。')
                self.require(cfg,actor,scope['business_project'])
                return workspace
        # Equivalent host paths must share one execution scope and artifact namespace.
        workspace = normalize_workspace_path(workspace, require_abs=False)
        with self.lock:
            cfg = self._read_config()
            bid, scope, changed = self._workspace_scope(cfg, actor, workspace, session)
            if not changed:
                return scope
            # Session binding is a write. Read an independent current config and
            # recompute authorization before editing it under the same lock.
            if self._snapshot_reads.get():
                cfg = self.config()
                bid, scope, changed = self._workspace_scope(cfg, actor, workspace, session)
                if not changed:
                    return scope
            cfg['scopes'][scope] = {'actor':actor,'workspace':workspace,'business_project':bid}
            if session:
                cfg['sessions'][actor+':'+session] = {'business_project':bid,'locked':True}
            self._write(cfg)
            return scope

    def _workspace_scope(self, cfg, actor, workspace, session):
        info = self._session_info(cfg, actor, session or '__workspace_preview__', workspace)
        bid = info['business_project']
        if not bid:
            raise PermissionError('请在设置 → 智能问数中接入数据库、选择项目授权表并校验发布；有多个授权项目时选择默认项目后新建会话。')
        if not info['published_version']:
            raise PermissionError('业务项目尚未发布。')
        digest = hashlib.sha256((actor+'\0'+workspace).encode()).hexdigest()[:24]
        scope = 'business/'+bid+'/'+digest
        return bid, scope, scope not in cfg['scopes'] or bool(session and not info['locked'])

    def policy_scope(self, scope):
        owner=self._read_config().get('scopes',{}).get(scope,{}).get('business_project')
        return 'business/'+owner if owner else policy_key(scope)

    def sources(self, scope, include_disabled=False):
        cfg = self._read_config(); entry = cfg['scopes'].get(scope)
        if not entry: return []
        project = self.require(cfg,entry['actor'],entry['business_project'])
        if project.get('archived') or not project['published_version']: return []
        version = next(v for v in project['versions'] if v['version']==project['published_version'])
        business = version['config']; result = []
        catalog = {source['id']: source for source in self.engine._read_config().get('sources', [])}
        for source in catalog.values():
            if source['id'] not in business['source_ids']: continue
            if not include_disabled and not source.get('connection_enabled',True): continue
            value = copy.deepcopy(project_source(source, version))
            value['tables'] = [t for t in value['tables'] if t['name'] in business['tables'].get(source['id'],[])]
            from .definition_evidence import scoped_terms
            value['terms'] = scoped_terms(business, source['id'], catalog)
            from .caliber_excel import excel_review_reason
            pending = set()
            for term in value['terms']:
                reason = excel_review_reason(term)
                if reason:
                    term.update(status='review', review_reason=reason)
                    pending.add(term.get('code') or (term.get('config') or {}).get('id'))
            value['examples'] = [copy.deepcopy(e) for e in business['examples'] if e['source_id']==source['id'] and e.get('metric_code') not in pending]
            value['business_project'] = {'id':project['id'],'name':project['name'],'version':version['version']}
            value['business_skills'] = copy.deepcopy(business['skills'])
            value['business_layers'] = copy.deepcopy(business['layers'])
            from .business_dictionary import apply_mappings
            apply_mappings(value,business.get('mappings',[]))
            result.append(value)
        return result
