"""Identity for corrected aggregates, retaining population and grouping scope."""
import hashlib
import sqlglot
from sqlglot import exp
from .vendor.dialect import sqlglot_name


def query_identity(sql, dialect, source_id):
    tree = sqlglot.parse_one(sql, read=sqlglot_name(dialect))
    # A corrected conditional aggregate changes its classification expression,
    # not its population. Retain function/field lineage and output names; all
    # WHERE, JOIN, period, region and grouping expressions stay unchanged.
    for select in tree.find_all(exp.Select):
        for projection in select.expressions:
            for agg in list(projection.find_all(exp.AggFunc)):
                target = agg.parent if isinstance(agg.parent, exp.Filter) else agg
                signature = agg.key + ':' + ','.join(sorted(c.sql() for c in target.find_all(exp.Column)))
                target.replace(exp.Literal.string(signature))
    return hashlib.sha256((source_id + '\n' + tree.sql()).encode()).hexdigest()[:24]
