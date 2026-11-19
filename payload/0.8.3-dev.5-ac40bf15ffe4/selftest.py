"""Offline package smoke tests; synthetic data, no model or network required."""
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent/'engine'))
from check_runtime import check
from natural_data.core import DataEngine  # registers SQLite's dialect
from natural_data.vendor.plans import PolicyContext, validate_plan
from natural_data.read_limits import bounded_query


def query(sql, dialect='postgresql'):
    return validate_plan({'mode':'sql','plan_id':'selftest','workspace_id':'selftest',
        'datasource_ref':'synthetic','logical_sql':sql,'expected_chart':{'type':'table','title':'selftest'}},
        PolicyContext(workspace_id='selftest',allowed_datasource_refs=['synthetic'],
            allowed_relations=['sales'],db_types={'synthetic':dialect}))


class PackageTests(unittest.TestCase):
    def test_compatible_environment(self): check(compatible=True)

    def test_legitimate_business_sql(self):
        for sql in ('SELECT comment FROM sales',"SELECT name FROM sales WHERE name='Set A'",
                    "SELECT name FROM sales WHERE name='moved into service'",'SELECT name AS do FROM sales'):
            query(sql)

    def test_side_effects_rejected(self):
        for sql in ('SELECT * INTO copied FROM sales','SELECT * FROM sales FOR UPDATE',
                    'WITH x AS (DELETE FROM sales RETURNING *) SELECT * FROM x'):
            with self.assertRaises(ValueError): query(sql)

    def test_mysql_variables_rejected(self):
        for var in ('@@version','@@global.datadir','@@session.hostname','@v := name'):
            with self.assertRaises(ValueError): query('SELECT '+var+' FROM sales','mysql')
        query("SELECT '@@version' FROM sales",'mysql')

    def test_database_applies_row_budget(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE sales(value)')
            db.executemany('INSERT INTO sales VALUES(?)',[(n,) for n in range(2000)])
            sql=bounded_query('SELECT value FROM sales ORDER BY value DESC','sqlite',500,{})
            rows=db.execute(sql).fetchall()
            self.assertEqual(len(rows),501)
            self.assertEqual(rows[0],(1999,))
            sql=bounded_query('SELECT value FROM sales ORDER BY value LIMIT 2 OFFSET 3','sqlite',500,{})
            self.assertEqual(db.execute(sql).fetchall(),[(3,),(4,)])


if __name__=='__main__': unittest.main(verbosity=2)
