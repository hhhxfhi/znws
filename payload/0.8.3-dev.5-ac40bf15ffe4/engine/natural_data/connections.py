"""Host-admin database configuration. Secrets never enter catalogs or tool args."""
import copy
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import ssl
import time
import uuid

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from natural_work.store import ConflictError
from .vendor.dialect import get_dialect

TYPES = {'postgresql': ('PostgreSQL', 5432, 'psycopg2'), 'tbase': ('TBase', 5432, 'psycopg2'),
         'mysql': ('MySQL', 3306, 'pymysql'), 'sqlite': ('SQLite', None, 'sqlite3'),
         'oracle': ('Oracle',1521,'oracledb'), 'dameng': ('达梦 DM',5236,'dmPython'),
         'kingbase': ('人大金仓 KingbaseES',54321,'ksycopg2')}
from .additional_databases import driver_status, physical_name

class MetadataError(ValueError):
    pass

def write_private(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)

def connection_url(engine, source):
    """Capture the exact revision's secret under the configuration lock."""
    with engine.lock:
        current = next((s for s in engine.config()['sources'] if s['id']==source['id']), None)
        keys = ('dialect','credential_ref','connection_revision','path','connection_schema')
        if current is None or any(current.get(k)!=source.get(k) for k in keys):
            raise ConflictError('数据库连接已变化，请重新获取目录并计划。')
        if source['dialect']=='sqlite': return None
        path = engine.home/'credentials.json'
        secrets = json.loads(path.read_text()) if path.exists() else {}
        try: return secrets[source['credential_ref']]
        except KeyError: raise ValueError('连接凭据缺失，请重新配置数据库连接。') from None

def legacy_mysql_sslmode(query):
    """Effective mode of a MySQL URL saved before sslmode existed (<= 0.7.4).

    Those URLs carried only ssl_ca/ssl_check_hostname; without a CA PyMySQL
    connected in plain text. Upgrading must not silently change that.
    """
    if 'sslmode' in query: return None
    if not query.get('ssl_ca'): return 'disable'
    return 'verify-full' if str(query.get('ssl_check_hostname', 'true')).lower() == 'true' else 'verify-ca'

def mysql_tls_options(url):
    parsed = make_url(url)
    legacy = legacy_mysql_sslmode(parsed.query)
    if legacy == 'disable': return {}
    mode = legacy or parsed.query.get('sslmode', 'verify-full')
    if mode == 'disable': return {'ssl_disabled': True}
    if mode not in {'require','verify-ca','verify-full'}: raise ValueError('MySQL SSL 模式无效。')
    context = ssl.create_default_context(cafile=parsed.query.get('ssl_ca') or None)
    context.check_hostname = mode == 'verify-full'
    if mode == 'require': context.verify_mode = ssl.CERT_NONE
    return {'ssl':context}

@contextmanager
def read_connection(source, url=None):
    """One bounded read-only connection for query, probe and metadata reflection."""
    kind = source['dialect']
    if kind == 'sqlite':
        path = Path(source['path']).resolve()
        if not path.is_file(): raise ValueError('SQLite 文件不存在，请填写已有数据库的绝对路径。')
        from .sqlite_connection import connect_readonly
        db = create_engine('sqlite://', poolclass=NullPool,
                           creator=lambda: connect_readonly(path, timeout=8))
        dialect = None
    else:
        dialect = get_dialect(kind)
        if dialect is None: raise ValueError('不支持此数据库类型。')
        options = dict(dialect.connect_args)
        if kind == 'mysql':
            options.update(read_timeout=30, write_timeout=8, **mysql_tls_options(url))
            # sslmode is our portable setting, not a PyMySQL connect argument.
            url = make_url(url).difference_update_query(['sslmode','ssl_ca','ssl_check_hostname'])
        if kind=='dameng':
            import dmPython
            options.update(access_mode=dmPython.DSQL_MODE_READ_ONLY,schema=source.get('connection_schema',''))
        db = create_engine(url, poolclass=NullPool, connect_args=options)
    try:
        with db.connect() as conn:
            if kind == 'sqlite':
                conn.exec_driver_sql('PRAGMA query_only=ON')
                start = time.monotonic()
                conn.connection.driver_connection.set_progress_handler(lambda: int(time.monotonic()-start>30),1000)
            else:
                for statement in dialect.session_setup(30): conn.exec_driver_sql(statement)
                if kind=='oracle':
                    conn.connection.driver_connection.call_timeout=30000
                    if source.get('connection_schema'): conn.exec_driver_sql('ALTER SESSION SET CURRENT_SCHEMA = '+dialect.quote(source['connection_schema']))
            if conn.in_transaction(): conn.commit()
            with conn.begin():
                if dialect:
                    for statement in dialect.transaction_setup(30): conn.exec_driver_sql(statement)
                    schema = source.get('connection_schema') or ('public' if kind!='mysql' else None)
                    if schema and kind in {'postgresql','postgis','tbase','kingbase'}: conn.exec_driver_sql('SET LOCAL search_path = '+dialect.quote(schema))
                yield conn
    finally:
        db.dispose()

class Connections:
    def __init__(self, engine, business):
        self.engine, self.business = engine, business
        self.credentials_path = engine.home/'credentials.json'
        self.deletion_path = engine.home/'pending-connection-delete.json'
        with self.engine.lock: self.recover_delete()

    def recover_delete(self):
        """Finish an interrupted configuration-only deletion before serving requests."""
        if not self.deletion_path.exists():
            self.prune_credentials()
            return
        pending=json.loads(self.deletion_path.read_text())
        # Revoke grants first: any interrupted write fails closed for new reads.
        self.business._write(pending['business'])
        write_private(self.engine.config_path,pending['sources'])
        for project,key in list(self.engine.active):
            with self.engine.connect() as c:
                row=c.execute('SELECT payload FROM runs WHERE project=? AND request_key=?',(project,key)).fetchone()
            if row and json.loads(row[0]).get('source_id')==pending['source_id']: self.engine.cancel(project,key)
        if self.credentials_path.exists():
            refs={s.get('credential_ref') for s in pending['sources']['sources']}
            removed=set(pending.get('credential_refs',[]))
            secrets={ref:value for ref,value in self.secrets().items() if ref in refs or ref not in removed}
            write_private(self.credentials_path,secrets)
        self.deletion_path.unlink()
        self.prune_credentials()

    def prune_credentials(self):
        """Recover either side of a secrets -> sources commit, including rotation."""
        self.credential_cleanup_pending = False
        try:
            with self.engine.lock: self._prune_credentials()
        except OSError:
            # The source commit may already be durable. Report the committed
            # revision and retry cleanup, instead of claiming the save failed.
            self.credential_cleanup_pending = True

    def _prune_credentials(self):
        # A killed process cannot run write_private's finally block. Such a
        # pre-rename file may hold old passwords even after normal rotation.
        for path in self.credentials_path.parent.glob(self.credentials_path.name+'.*.tmp'):
            if re.fullmatch(re.escape(self.credentials_path.name)+r'\.[0-9a-f]{32}\.tmp',path.name):
                path.unlink(missing_ok=True)
        cfg = self.engine.config()
        refs = {s.get('credential_ref') for s in cfg['sources']}
        secrets = self.secrets()
        live = {ref:value for ref,value in secrets.items() if ref in refs}
        if live != secrets: write_private(self.credentials_path, live)
        changed = any('retired_credential_refs' in s for s in cfg['sources'])
        for source in cfg['sources']: source.pop('retired_credential_refs', None)
        if changed: write_private(self.engine.config_path, cfg)

    def require(self, actor):
        if actor not in self.business.config()['admins']:
            raise PermissionError('只有系统管理员可以维护数据库连接。')

    def secrets(self):
        return json.loads(self.credentials_path.read_text()) if self.credentials_path.exists() else {}

    def url(self, source):
        return connection_url(self.engine, source)

    def source(self, cfg, sid):
        source = next((s for s in cfg['sources'] if s['id']==sid), None)
        if source is None: raise ValueError('数据库连接不存在。')
        return source

    @staticmethod
    def used_by(source, projects):
        return [project['name'] for project in projects.values() if not project.get('archived') and (
            source['id'] in project['draft']['source_ids'] or any(
                source['id'] in version['config']['source_ids'] for version in project['versions']
                if version['version'] == project['published_version']))]

    def descriptor(self, source, projects, *, credentials):
        target = {'path': source.get('path','')} if source['dialect']=='sqlite' else {}
        if source['dialect']!='sqlite':
            try:
                url = make_url(credentials[source['credential_ref']])
                target = {'host':url.host or '', 'port':url.port or TYPES.get(source['dialect'],('',5432))[1],
                          'database':url.database or url.query.get('service_name') or source.get('connection_database',''), 'username':url.username or '', 'password_set':bool(url.password),
                          'sslmode':url.query.get('sslmode','verify-full' if source['dialect']=='mysql' else 'prefer'),
                          'ssl_ca':url.query.get('ssl_ca',url.query.get('sslrootcert',''))}
                legacy = legacy_mysql_sslmode(url.query) if source['dialect']=='mysql' else None
                if legacy: target.update(sslmode=legacy, legacy_tls=True)
                if source['dialect'] in {'postgresql','postgis','tbase','kingbase','mysql'}:
                    target['tls_warning'] = '' if target['sslmode']=='verify-full' else (
                        '此连接沿用升级前的加密设置，未同时强制加密、校验证书和主机名；建议在连接设置中改为 verify-full 并测试连接。'
                        if legacy else '当前模式未同时强制加密、校验证书和主机名；建议使用 verify-full。')
            except (ValueError,KeyError): target = {'password_set':False}
        target['schema'] = source.get('connection_schema','public' if source['dialect'] in {'postgresql','postgis','tbase'} else target.get('database','') if source['dialect']=='mysql' else '')
        return {**{k:source[k] for k in ('id','name','dialect','description','origin','synthetic','historical_qualification') if k in source},
                'connection':target, 'managed':bool(source.get('connection_managed')), 'enabled':source.get('connection_enabled',True),
                'table_count':len(source['tables']), 'tables':[{'name':t['name'],'label':t.get('label',''),'columns':t.get('columns',[])} for t in source['tables']],
                'used_by':self.used_by(source, projects), 'last_checked':source.get('connection_checked_at'), 'connection_revision':source.get('connection_revision',0),
                'schema_changes':source.get('schema_changes',{}), 'target_revision':source.get('target_revision',0)}

    def snapshot(self, actor):
        with self.engine.lock:
            business = self.business.config()
            if actor not in business['admins']:
                raise PermissionError('只有系统管理员可以维护数据库连接。')
            if self.deletion_path.exists():
                self.recover_delete()
                business = self.business.config()
            elif self.credential_cleanup_pending:
                self.prune_credentials()
            cfg = self.engine.config()
            projects = business['projects']
            # This is a display snapshot under the same lock as connection
            # updates. Runtime connections still validate the exact revision.
            credentials = self.secrets() if any(s['dialect'] != 'sqlite' for s in cfg['sources']) else {}
            return {'revision':cfg.get('connections_revision',0), 'business_revision':business['revision'],
                    'credential_cleanup_pending':self.credential_cleanup_pending,
                    **({'message':'连接配置已保存；旧凭据清理尚未完成，请检查私有数据目录写入权限。下次访问会重试清理。'} if self.credential_cleanup_pending else {}),
                    'types':[{'id':key,'label':v[0],'port':v[1],**driver_status(key,v[2])} for key,v in TYPES.items()],
                    'connections':[self.descriptor(s, projects, credentials=credentials) for s in cfg['sources']]}

    def candidate(self, cfg, args):
        old = self.source(cfg,args['source_id']) if args.get('source_id') else None
        values = args.get('connection')
        if not isinstance(values,dict): raise ValueError('请填写数据库连接配置。')
        allowed = {'name','description','dialect','host','port','database','username','password','password_action','path','schema','sslmode','ssl_ca','synthetic','historical_qualification'}
        if set(values)-allowed: raise ValueError('连接配置含不支持的字段。')
        kind = values.get('dialect')
        if kind not in TYPES: raise ValueError('不支持此数据库类型。')
        if not driver_status(kind,TYPES[kind][2])['available']: raise ValueError('此数据库驱动未安装，请先安装运行依赖。')
        def string(key, maximum=500, required=False):
            value = values.get(key,'')
            if not isinstance(value,str) or len(value)>maximum or '\x00' in value: raise ValueError('数据库连接字段格式无效。')
            value = value.strip()
            if required and not value: raise ValueError('请补全数据库名称、地址和账号等必填字段。')
            return value
        source = copy.deepcopy(old) if old else {'id':'db-'+uuid.uuid4().hex[:16],'origin':'自然 Work','tables':[],'terms':[],'examples':[]}
        source.update(name=string('name',100,True),description=string('description',1000),dialect=kind,connection_managed=True)
        synthetic = values.get('synthetic',old.get('synthetic',False) if old else False)
        if type(synthetic) is not bool: raise ValueError('模拟数据标记格式无效。')
        source['synthetic'] = synthetic
        source['connection_revision'] = source.get('connection_revision',0)+1
        source['connection_enabled'] = source.get('connection_enabled',True)
        source.pop('retired_credential_refs', None)
        url = None
        if kind=='sqlite':
            path = Path(string('path',4096,True)).expanduser()
            if not path.is_absolute() or not path.is_file(): raise ValueError('SQLite 文件不存在，请填写已有数据库的绝对路径。')
            source['path'] = str(path.resolve()); source.pop('credential_ref',None); source.pop('connection_schema',None)
        else:
            host, database, username = string('host',253,True),string('database',128,True),string('username',128,True)
            if kind=='oracle' and username.strip(chr(34)).upper()=='SYS': raise ValueError('Oracle SYS 不支持本产品要求的只读事务，请使用仅有查询权限的业务账号。')
            if any(c in host for c in '/@?# \t\r\n'): raise ValueError('数据库地址只能填写主机名或 IP，不能填写连接 URL。')
            port = values.get('port')
            if type(port) is not int or not 1<=port<=65535: raise ValueError('端口必须是 1—65535 的整数。')
            action = values.get('password_action','replace')
            password = values.get('password','')
            if action not in {'keep','replace'} or not isinstance(password,str) or len(password)>4096 or '\x00' in password: raise ValueError('密码配置无效。')
            if action=='keep':
                if not old or old['dialect']=='sqlite': raise ValueError('新连接需要填写密码或明确使用空密码。')
                password = make_url(self.url(old)).password or ''
            schema = string('schema',128) or (username.upper() if kind in {'oracle','dameng'} else database if kind=='mysql' else 'public')
            if not re.fullmatch(r'[\w$-]{1,128}',schema): raise ValueError('Schema 请使用文字、数字、下划线或连字符。')
            if kind=='mysql' and schema!=database: raise ValueError('MySQL 的 Schema 与数据库名称必须一致。')
            source['connection_schema'] = schema
            source['connection_database'] = database
            dialect = get_dialect(kind)
            url = dialect.engine_url(username,password,host,port,database)
            if kind in {'postgresql','postgis','tbase','kingbase'}:
                mode = values.get('sslmode','verify-full')
                if mode not in {'disable','prefer','require','verify-ca','verify-full'}: raise ValueError('SSL 模式无效。')
                url = url.update_query_dict({'sslmode':mode})
                ca = string('ssl_ca',4096)
                if ca:
                    if not Path(ca).is_absolute() or not Path(ca).is_file(): raise ValueError('CA 证书文件不存在，请填写本机绝对路径。')
                    url = url.update_query_dict({'sslrootcert':ca})
            elif kind=='mysql':
                mode = values.get('sslmode','verify-full')
                if mode not in {'disable','require','verify-ca','verify-full'}: raise ValueError('MySQL SSL 模式无效。')
                url = url.update_query_dict({'sslmode':mode})
                ca = string('ssl_ca',4096)
                if ca:
                    if not Path(ca).is_absolute() or not Path(ca).is_file(): raise ValueError('CA 证书文件不存在，请填写本机绝对路径。')
                    url = url.update_query_dict({'ssl_ca':ca})
            source['credential_ref'] = 'connection:'+uuid.uuid4().hex
            source.pop('path',None)
        # A different target cannot inherit metric mappings audited for the old DB.
        old_identity = None
        if old:
            try: old_identity = self.identity(old,self.url(old))
            except ValueError: pass  # Missing credentials must remain repairable.
        if old and old_identity != self.identity(source,url):
            for field in ('metric_mapping','coverage','adapter_review','historical_qualification'): source.pop(field,None)
            source['target_revision'] = old.get('target_revision',0)+1
            using = any(old['id'] in p['draft']['source_ids'] or any(old['id'] in v['config']['source_ids'] for v in p['versions'] if v['version']==p['published_version']) for p in self.business.config()['projects'].values())
            if using and args.get('confirm_target_change') is not True:
                raise ValueError('连接目标变化会影响已接入的业务项目，请确认切换后重新保存。')
        if 'historical_qualification' in values:
            from .historical_qualification import validate_qualification_config
            metadata = validate_qualification_config(values['historical_qualification'])
            if metadata is None: source.pop('historical_qualification', None)
            else: source['historical_qualification'] = metadata
        return source,url

    @staticmethod
    def identity(source,url):
        if source['dialect']=='sqlite': return ('sqlite',source['path'])
        parsed = make_url(url)
        return (source['dialect'],parsed.host,parsed.port or TYPES.get(source['dialect'],('',5432))[1],parsed.database or parsed.query.get('service_name') or source.get('connection_database',''),
                source.get('connection_schema') or (parsed.database if source['dialect']=='mysql' else 'public'))

    def inspect(self, source, url, discover=False):
        try:
            with read_connection(source,url) as conn:
                conn.execute(text('SELECT 1 FROM DUAL' if source['dialect'] in {'oracle','dameng'} else 'SELECT 1')).fetchone()
                if not discover: return []
                metadata = inspect(conn)
                schema = None if source['dialect']=='sqlite' else source.get('connection_schema') or ('public' if source['dialect']!='mysql' else make_url(url).database)
                names = sorted(set(metadata.get_table_names(schema=schema)+metadata.get_view_names(schema=schema)))
                if len(names)>1000: raise MetadataError('此 Schema 超过 1000 张表，请配置更小的数据范围。')
                tables = []
                for name in names:
                    columns = metadata.get_columns(name,schema=schema)
                    fold=lambda n:physical_name(n,conn.dialect) if source['dialect'] in {'oracle','dameng'} else str(n)
                    physical=fold(name)
                    pk=metadata.get_pk_constraint(name,schema=schema).get('constrained_columns') or []
                    unique=[u.get('column_names',[]) for u in metadata.get_unique_constraints(name,schema=schema)]
                    foreign=metadata.get_foreign_keys(name,schema=schema)
                    for fk in foreign:
                        for key in ('constrained_columns','referred_columns'):
                            fk[key]=[fold(c) for c in fk.get(key,[])]
                        if fk.get('referred_table'):fk['referred_table']=fold(fk['referred_table'])
                    tables.append({'name':physical if source['dialect']=='sqlite' else schema+'.'+physical,'label':'',
                                   'columns':[{'name':fold(c['name']),'type':str(c['type']),'label':c.get('comment') or ''} for c in columns],
                                   'primary_key':[fold(c) for c in pk],
                                   'unique_keys':[[fold(c) for c in keys] for keys in unique],
                                   'foreign_keys':foreign})
                if not tables: raise MetadataError('连接成功，但指定 Schema 没有可读取的表或视图。')
                return tables
        except MetadataError: raise
        except Exception:
            # Driver diagnostics frequently include URLs, account names and SQL.
            raise ValueError('数据库连接或结构读取失败，请检查地址、端口、数据库、账号权限及 SSL 配置。') from None

    def test(self, actor, args):
        self.require(actor)
        with self.engine.lock:
            cfg = self.engine.config()
            if 'connection' in args: source,url = self.candidate(cfg,args)
            else: source = copy.deepcopy(self.source(cfg,args.get('source_id'))); url = self.url(source)
        self.inspect(source,url)
        return {'connected':True,'message':'数据库连接成功；尚未改变项目授权。'}

    def update(self, actor, args):
        self.require(actor)
        action = args.get('action')
        # Network work does not hold the shared query-admission lock. CAS is
        # checked again before committing; a concurrent save cannot be overwritten.
        with self.engine.lock:
            self.recover_delete()
            cfg = self.engine.config()
            if args.get('expected_revision')!=cfg.get('connections_revision',0): raise ConflictError('数据库连接已被修改，请刷新后重试。')
            if action == 'delete':
                source = self.source(cfg,args.get('source_id'))
                if args.get('confirm_delete') is not True: raise ValueError('请确认删除数据库连接。')
                business=self.business.config()
                using = self.used_by(source, business['projects'])
                if using:
                    if args.get('confirm_detach') is not True:
                        raise ValueError('连接仍被业务项目使用：'+'、'.join(using)+'。请确认同时移除项目中的数据源引用。')
                    if args.get('expected_business_revision')!=business['revision']:
                        raise ConflictError('业务项目引用已变化，请重新查看受影响项目后确认删除。')
                sid=source['id']
                def detach(config):
                    value=copy.deepcopy(config)
                    value['source_ids']=[i for i in value['source_ids'] if i!=sid]
                    value['tables'].pop(sid,None)
                    value['examples']=[row for row in value['examples'] if row.get('source_id')!=sid]
                    value['terms']=[row for row in value['terms'] if (row.get('config', {}).get('source_id') or row.get('source_id')) != sid]
                    return value
                changed=False
                for project in business['projects'].values():
                    draft=detach(project['draft'])
                    if draft!=project['draft']: project['draft']=draft;changed=True
                    published=next((v for v in project['versions'] if v['version']==project['published_version']),None)
                    if not project.get('archived') and published and sid in published['config']['source_ids']:
                        version=max(v['version'] for v in project['versions'])+1
                        project['versions'].append({'version':version,'config':detach(published['config']),'actor':actor,'note':'删除数据库连接：'+source['name'],'at':time.time()})
                        project['published_version']=version;changed=True
                if changed: business['revision']+=1
                cfg['sources'] = [s for s in cfg['sources'] if s['id']!=sid]
                cfg['deleted_source_ids'] = sorted(set(cfg.get('deleted_source_ids',[]))|{sid})
                for ids in cfg.get('projects',{}).values():
                    if sid in ids: ids.remove(sid)
                cfg['connections_revision'] = cfg.get('connections_revision',0)+1
                # One durable intent makes the two config files and secret cleanup
                # replayable after a crash without rewriting historical versions.
                write_private(self.deletion_path,{'source_id':sid,'credential_refs':[ref for ref in [source.get('credential_ref'),*source.get('retired_credential_refs',[])] if ref],'sources':cfg,'business':business})
                self.recover_delete()
                return {**self.snapshot(actor),'selected_source':''}
            if action == 'metadata':
                source = self.source(cfg,args.get('source_id'))
                name,description=args.get('name'),args.get('description','')
                if not isinstance(name,str) or not name.strip() or len(name)>100 or not isinstance(description,str) or len(description)>1000:
                    raise ValueError('请填写有效的连接名称和说明。')
                source.update(name=name.strip(),description=description.strip(),connection_managed=True)
                cfg['connections_revision']=cfg.get('connections_revision',0)+1
                write_private(self.engine.config_path,cfg)
                return {**self.snapshot(actor),'selected_source':source['id']}
            if action=='save': source,url = self.candidate(cfg,args)
            elif action in {'refresh','state'}:
                source = copy.deepcopy(self.source(cfg,args.get('source_id'))); url = self.url(source) if action=='refresh' else None
                source['connection_revision'] = source.get('connection_revision',0)+1
                source['connection_managed'] = True
            else: raise ValueError('未知数据库连接操作。')
        if action in {'save','refresh'}:
            discovered = self.inspect(source,url,discover=True)
            previous = {t['name']:t for t in source['tables']}
            for table in discovered:
                # Preserve existing relation identifiers when adopting an
                # imported connection; refreshing metadata must not silently
                # revoke its projects' bare-table grants or metric contracts.
                if table['name'] not in previous and table['name'].split('.')[-1] in previous:
                    table['name'] = table['name'].split('.')[-1]
                old = previous.get(table['name'],{})
                table['label'] = old.get('label','')
                old_columns = {c['name']:c for c in old.get('columns',[])}
                for column in table['columns']:
                    if column['name'] in old_columns: column['label'] = old_columns[column['name']].get('label',column.get('label',''))
            source['tables'] = discovered; source['connection_checked_at'] = time.time()
            source['schema_changes'] = {'added_tables': sorted({t['name'] for t in discovered}-set(previous)),
                'removed_tables': sorted(set(previous)-{t['name'] for t in discovered}),
                'changed_tables': [t['name'] for t in discovered if t['name'] in previous and
                    {c['name'] for c in t['columns']}!={c['name'] for c in previous[t['name']]['columns']}]}
        else:
            if type(args.get('enabled')) is not bool: raise ValueError('连接启用状态无效。')
            source['connection_enabled'] = args['enabled']
        with self.engine.lock:
            latest = self.engine.config()
            if args['expected_revision']!=latest.get('connections_revision',0): raise ConflictError('数据库连接已被修改，请刷新后重试。')
            # Commit new secret before its ref. Queries already admitted hold an
            # in-memory URL; stale snapshots are refused by connection_url().
            if source['dialect']!='sqlite' and action=='save':
                secrets = self.secrets(); secrets[source['credential_ref']] = url.render_as_string(hide_password=False)
                write_private(self.credentials_path,secrets)
            latest['sources'] = [source if s['id']==source['id'] else s for s in latest['sources']]
            if not any(s['id']==source['id'] for s in latest['sources']): latest['sources'].append(source)
            latest['connections_revision'] = latest.get('connections_revision',0)+1
            write_private(self.engine.config_path,latest)
            self.prune_credentials()
            # Connection changes invalidate pending plans via catalog revision;
            # stop existing reads through this source as well.
            with self.engine.connect() as c:
                rows = c.execute("SELECT project,request_key,payload FROM runs WHERE status='running'").fetchall()
            for project,key,payload in rows:
                if json.loads(payload).get('source_id')==source['id']: self.engine.cancel(project,key)
            return {**self.snapshot(actor),'selected_source':source['id']}
