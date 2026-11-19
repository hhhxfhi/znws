"""Optional vendor dialects. Driver readiness never means target DB acceptance."""
import importlib
from sqlalchemy import URL
from sqlalchemy.dialects import registry


def driver_status(kind, module):
    try:
        importlib.import_module(module)
        driver={'oracle':'oracle.oracledb','dameng':'dm.dmPython','kingbase':'kingbase.ksycopg2'}.get(kind)
        if driver: registry.load(driver)
    except Exception:
        return {'available':False,'status':'driver_missing','status_label':'需安装并校验厂商驱动及方言'}
    return {'available':True,'status':'driver_loadable','status_label':'驱动可加载，目标库另行验证'}


def register(base, postgres):
    class Oracle(base):
        key='oracle'
        driver='oracle+oracledb'
        sqlglot='oracle'
        default_schema=''
        connect_args={'tcp_connect_timeout':8}

        def engine_url(self,username,password,host,port,database):
            return URL.create(self.driver,username=username,password=password,host=host,port=port,
                              query={'service_name':database})

        def transaction_setup(self,timeout_seconds): return ['SET TRANSACTION READ ONLY']
        def quote(self,identifier): return '"'+identifier.replace('"','""')+'"'

        def columns_sql(self):
            return ('SELECT c.COLUMN_NAME AS column_name,c.DATA_TYPE AS data_type,c.DATA_TYPE AS udt_name,'
                    'c.NULLABLE AS is_nullable,c.COLUMN_ID AS ordinal_position,m.COMMENTS AS comment '
                    'FROM ALL_TAB_COLUMNS c LEFT JOIN ALL_COL_COMMENTS m ON m.OWNER=c.OWNER '
                    'AND m.TABLE_NAME=c.TABLE_NAME AND m.COLUMN_NAME=c.COLUMN_NAME '
                    'WHERE c.OWNER=:schema_name AND c.TABLE_NAME=:table_name ORDER BY c.COLUMN_ID')

        def constraints_sql(self):
            return ("SELECT CASE c.CONSTRAINT_TYPE WHEN 'P' THEN 'PRIMARY KEY' WHEN 'U' THEN 'UNIQUE' "
                    "ELSE 'FOREIGN KEY' END AS constraint_type,c.CONSTRAINT_NAME AS constraint_name,"
                    'k.COLUMN_NAME AS column_name,k.POSITION AS ordinal_position '
                    'FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS k ON c.OWNER=k.OWNER '
                    'AND c.CONSTRAINT_NAME=k.CONSTRAINT_NAME '
                    "WHERE c.OWNER=:schema_name AND c.TABLE_NAME=:table_name AND c.CONSTRAINT_TYPE IN ('P','U','R')")

        def table_comment_sql(self): return 'SELECT COMMENTS FROM ALL_TAB_COMMENTS WHERE OWNER=:schema_name AND TABLE_NAME=:table_name'
        def database_type(self,row): return str(row['data_type'])
        def version_sql(self): return "SELECT SYS_CONTEXT('USERENV','DB_NAME'),USER FROM DUAL"

    class Dameng(Oracle):
        key='dameng'
        driver='dm+dmPython'
        connect_args={'login_timeout':8000,'connection_timeout':30,'autoCommit':False}

        def engine_url(self,username,password,host,port,database):
            # A DM instance is addressed by host/port; schema is a connect option.
            return URL.create(self.driver,username=username,password=password,host=host,port=port)

        def version_sql(self): return 'SELECT NAME,USER FROM V$DATABASE'

    class Kingbase(postgres):
        key='kingbase'
        driver='kingbase+ksycopg2'
        sqlglot='postgres'
        supports_gis=False

    return {'oracle':Oracle(),'dameng':Dameng(),'kingbase':Kingbase()}


def physical_name(name, dialect):
    """Undo ORM normalization, preserving explicitly quoted lowercase identifiers."""
    if getattr(name,'quote',None) is True: return str(name)
    return str(dialect.denormalize_name(name)) if hasattr(dialect,'denormalize_name') else str(name)
