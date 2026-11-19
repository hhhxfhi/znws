"""Definition provenance is not a model claim and is not numerical acceptance."""
import copy
import re
from .catalog_index import fingerprint


_IDENTIFIER = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"\r\n]+"|`[^`\r\n]+`|\[[^\]\r\n]+\])'
_IDENTIFIER_PART = re.compile(_IDENTIFIER)
_RELATION = rf'{_IDENTIFIER}(?:[ \t]*\.[ \t]*{_IDENTIFIER})*'
_DOTTED_FIELD = re.compile(
    rf'(?<![A-Za-z0-9_$./:@]){_IDENTIFIER}(?:[ \t]*\.[ \t]*{_IDENTIFIER})+'
    r'(?![A-Za-z0-9_$./:@])'
)
_MARKED_RELATION = re.compile(
    rf'(?:表|月报|台账|\b(?i:FROM|JOIN)\b)[ \t]*(?P<after>{_RELATION})(?![A-Za-z0-9_$./:@])'
    rf'|(?<![A-Za-z0-9_$./:@])(?P<before>{_RELATION})[ \t]*(?:表|月报|台账)'
)


def _parts(name):
    return [part.strip('"`[]').casefold() for part in _IDENTIFIER_PART.findall(name)]


def _physical_dependencies(term, known_tables):
    declared_tables = term.get('tables')
    dependencies = set()
    if isinstance(declared_tables, list):
        dependencies.update((name.casefold(), None) for name in declared_tables if isinstance(name, str) and name)
    for field in ('name', 'definition'):
        text = term.get(field) or ''
        for match in _DOTTED_FIELD.finditer(text):
            parts = _parts(match[0])
            if parts[-2] in known_tables:
                dependencies.add(('.'.join(parts[:-1]), parts[-1]))
        for match in _MARKED_RELATION.finditer(text):
            parts = _parts(match['after'] or match['before'])
            if parts[-1] in known_tables:
                dependencies.add(('.'.join(parts), None))
    return dependencies


def _matches_legacy_scope(term, source_id, grants, known_tables, columns):
    dependencies = _physical_dependencies(term, known_tables)
    if not dependencies:
        return True
    possible = set(grants)
    for relation, column in dependencies:
        qualified = '.' in relation
        matched = {sid: [name for name in grants[sid]
                         if (name if qualified else name.rsplit('.', 1)[-1]).casefold() == relation]
                   for sid in possible}
        possible = {sid for sid, names in matched.items() if len(names) == 1}
        if source_id not in possible:
            return False
        if columns is not None:
            table_columns = columns.get(matched[source_id][0])
            if table_columns is None or (column is not None and column not in table_columns):
                return False
    return possible == {source_id}


def scoped_terms(config, source_id, catalog=None):
    """Old pack glossaries lacked source_id. Recover only a unique pack binding.

    Global business wording stays visible in each source. Legacy physical
    references need one matching authorized source. Inference changes only
    catalog visibility and does not grant access or rewrite the publication.
    """
    grants = config.get('tables') or {}
    known_tables = {name.casefold().rsplit('.', 1)[-1] for names in grants.values() for name in names}
    columns = None
    if catalog is not None:
        # The discovery inventory recognizes stale physical references after an
        # administrator revokes a grant. Only the publication's grants can bind.
        known_tables.update(table['name'].casefold().rsplit('.', 1)[-1]
                            for source in catalog.values() for table in source.get('tables', []))
        columns = {table['name']: {col['name'].casefold() for col in table.get('columns', [])}
                   for table in catalog.get(source_id, {}).get('tables', [])}
    owners = {}
    for group in ('terms', 'examples'):
        for row in config.get(group, []):
            origin = row.get('origin', '')
            sid = (row.get('config') or {}).get('source_id') or row.get('source_id')
            if origin.startswith('pack:') and sid:
                owners.setdefault(origin.split('@')[0], set()).add(sid)
    result = []
    for row in config.get('terms', []):
        sid = (row.get('config') or {}).get('source_id') or row.get('source_id')
        if sid and sid != source_id:
            continue
        if not sid and row.get('origin', '').startswith('pack:'):
            candidates = owners.get(row['origin'].split('@')[0], set())
            if candidates != {source_id}:
                continue
        elif not sid and not _matches_legacy_scope(row, source_id, grants, known_tables, columns):
            continue
        result.append(copy.deepcopy(row))
    return result


def definition_evidence(source, contract, spec=None):
    """Only an existing authenticated review of this exact spec is confirmed."""
    from .caliber_excel import reviewed
    term = next((t for t in source.get('terms', [])
                 if spec and t.get('config') == spec), None)
    record = term.get('reviewed') if term and reviewed(term) else None
    pending = not spec or any(word in str(contract.get('definition', '')) for word in ('推定', '待确认'))
    state = 'confirmed' if record else 'pending' if pending else 'configured'
    return {
        'state': state,
        'basis': 'administrator_review' if record else 'published_configuration' if spec else 'registered_compatibility_mapping',
        'source_id': source['id'], 'metric_id': contract['id'],
        'definition_version': contract['version'],
        'project_version': source.get('business_project', {}).get('version'),
        'definition_fingerprint': fingerprint(spec or {k: contract.get(k) for k in ('formula', 'source_mapping', 'mapping_version')}),
        'review': copy.deepcopy(record),
        'label': '业务定义已人工确认' if record else '业务定义待确认' if pending else '配置已生效；未记录单独业务确认',
        'numerical_acceptance': 'not_evaluated',
    }
