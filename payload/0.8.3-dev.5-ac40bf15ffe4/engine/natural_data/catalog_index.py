"""Versioned lexical retrieval over the current *authorized* catalog.

References are identities, never authority. Every read resolves against today's
catalog, so removed tables/columns/configuration cannot survive in a cache.
"""
import copy
import hashlib
import json
import re
from base64 import urlsafe_b64encode
from collections import defaultdict, deque

from .retrieval import rank, named


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), default=str).encode()).hexdigest()


def read_request(source_id, ref, version):
    """Encode an entry-bound version without asking the model to copy a full SHA."""
    digest = bytes.fromhex(fingerprint([source_id, ref, version]))[:16]
    token = 'v1.' + urlsafe_b64encode(digest).decode().rstrip('=')
    return {'source_id': source_id, 'entry_ref': ref, 'entry_version': token}


def _entry_ref(source_id, kind, table, identity):
    return kind + ':' + fingerprint([source_id, table, identity])[:24]


def entries(source):
    rows = []
    names = {t['name'] for t in source.get('tables', [])}

    def add(kind, value, identity=None, table=None):
        identity = str(identity or value.get('id') or value.get('name') or value.get('question') or fingerprint(value))
        ref = _entry_ref(source['id'], kind, table, identity)
        rows.append({'entry_ref': ref, 'kind': kind, 'source_id': source['id'],
                     'table': table, 'content': copy.deepcopy(value)})

    for table in source.get('tables', []):
        add('table', table, table=table['name'])
        for column in table.get('columns', []):
            add('field', column, table=table['name'])
    for kind, key in [('term', 'terms'), ('example', 'examples'), ('metric', 'semantic_metrics'),
                      ('metric_definition', 'metric_definitions')]:
        for value in source.get(key, []) or []:
            if value.get('source_id') not in (None, '', source['id']):
                continue
            identity = str(value.get("id")) + "@" + str(value["version"]) if kind in ("metric", "metric_definition") and value.get("id") and value.get("version") else None
            add(kind, value, identity=identity)
    for value in source.get('business_dictionary', []) or []:
        if value.get('kind') == 'relation' and {value.get('table'), value.get('to_table')} <= names:
            add('relation', value)
    return rows


def ranked(source, question, kinds=None):
    rows = [x for x in entries(source) if kinds is None or x['kind'] in kinds]
    def exact(row, query):
        value = row['content']
        physical = str(value.get('name') or '') if row['kind'] in ('table', 'field') else ''
        if physical and re.search(r'(?<![\w])' + re.escape(physical) + r'(?![\w])', query, re.I):
            return 2
        return int(named(value, query))
    output = []
    for priority, score, row in rank(rows, question,
            lambda row: [(json.dumps(row['content'], ensure_ascii=False), 1)], exact):
        if not question or priority or score:
            output.append({**row, 'rank': (priority, score),
                           'retrieval_basis': 'explicit_name' if priority == 2 else 'business_name_or_alias' if priority else 'bm25'})
    return output


def summaries(source, question, limit=12, *, version=None, exclude=()):
    version = version or fingerprint(source)
    result = []
    for row in ranked(source, question):
        if len(result) >= limit:
            break
        if row['entry_ref'] in exclude:
            continue
        content = row['content']
        result.append({k: row[k] for k in ('entry_ref', 'kind', 'source_id', 'table', 'retrieval_basis')})
        result[-1].update(config_fingerprint=version, complete=False,
                          summary=str(content.get('label') or content.get('name') or content.get('question') or content.get('definition') or '')[:160],
                          read_request=read_request(source['id'], row['entry_ref'], version))
    return result


def resolve_table_name(source, name):
    """Resolve an exact qualified name or an unambiguous bare name in this source."""
    names = [t['name'] for t in source.get('tables', [])]
    matches = [n for n in names if (n == name if '.' in name else n.rsplit('.', 1)[-1] == name)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        sid = source['id']
        options = json.dumps([{'source_id': sid, 'table_name': n} if '.' in n else
                              {'source_id': sid, 'entry_ref': _entry_ref(sid, 'table', n, n)}
                              for n in sorted(set(matches))[:6]], ensure_ascii=False)
        raise ValueError('CATALOG_TABLE_NOT_FOUND: 此来源有多个同名授权表，请核对后选择以下准确名称之一传给 data_catalog：' + options)
    retry = json.dumps({'source_id': source['id']}, ensure_ascii=False)
    raise ValueError('CATALOG_TABLE_NOT_FOUND: 该来源没有此授权表。请调用 data_catalog(' + retry + ')，复制目录中的准确表名。')


def read_entry(source, ref, expected=None, offset=0, limit=6000):
    sid = source['id']
    version = fingerprint(source)
    matches = []
    if ref.startswith('table:'):
        selector = ref[6:]
        # Do not reinterpret a removed opaque reference as a physical name.
        opaque = re.fullmatch(r'[0-9a-f]{24}', selector)
        for table in source.get('tables', []):
            name = table['name']
            if not opaque and selector != name:
                continue
            candidate = _entry_ref(sid, 'table', name, name)
            if not opaque or ref == candidate:
                matches.append({'entry_ref': candidate, 'kind': 'table', 'source_id': sid,
                                'table': name, 'content': table})
        if len(matches) == 1:
            ref = matches[0]['entry_ref']
    request = read_request(sid, ref, version)
    if ((offset > 0 and expected is None)
            or (expected is not None and expected not in (version, request['entry_version']))):
        retry = request if len(matches) == 1 else {'source_id': sid}
        raise ValueError('STALE_CATALOG_ENTRY: 读取版本缺失、无效或已过期。请从第一页重新调用 data_catalog('
                         + json.dumps(retry, ensure_ascii=False) + ')，按返回的 read_request 继续读取，不能拼接旧页面。')
    if not ref.startswith('table:'):
        matches = [x for x in entries(source) if x['entry_ref'] == ref]
    if len(matches) != 1:
        retry = json.dumps({'source_id': sid}, ensure_ascii=False)
        raise ValueError('CATALOG_ENTRY_NOT_FOUND: 当前授权目录不存在唯一的该条目。请调用 data_catalog(' + retry
                         + ') 后复制 read_request，表字段也可使用 table_name 加准确表名。')
    row = matches[0]
    value = row.pop('content')
    content = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if offset > len(content):
        raise ValueError('CATALOG_ENTRY_OFFSET: 分页偏移超出条目长度。')
    end = min(len(content), offset + min(limit, 6000))
    complete = offset == 0 and end == len(content)
    if complete and row['kind'] == 'table':
        value = copy.deepcopy(value)
    return {**row, 'config_fingerprint': version, 'read_request': request, 'complete': complete, 'offset': offset,
            'total_characters': len(content), 'content_text': content[offset:end],
            **({'content': value} if complete else {}),
            'next_offset': end if end < len(content) else None,
            'note': '完整条目' if complete else '分页内容，尚非完整条目；按 offset 顺序读取并拼接全部 content_text。'}


def relation_paths(source, question, table_name=None):
    """Find paths before applying display limits; expose alternatives, never choose one."""
    relations = [r['content'] for r in entries(source) if r['kind'] == 'relation']
    hit = [x['table'] for x in ranked(source, question, {'table', 'field'}) if x['rank'][0] > 0]
    if table_name:
        hit.insert(0, table_name)
    hit = list(dict.fromkeys(hit))[:8]
    graph = defaultdict(list)
    for i, r in enumerate(relations):
        graph[r['table']].append((r['to_table'], i))
        graph[r['to_table']].append((r['table'], i))
    paths, bounded = [], False
    for index, start in enumerate(hit):
        for target in hit[index + 1:]:
            queue = deque([(start, [start], [])])
            found, examined = [], 0
            while queue and len(found) < 8 and examined < 2000:
                current, visited, edges = queue.popleft()
                examined += 1
                if current == target:
                    found.append({'tables': visited, 'relations': edges})
                    continue
                if len(edges) >= 6:
                    bounded = True
                    continue
                for neighbor, edge in graph[current]:
                    if neighbor not in visited:
                        queue.append((neighbor, [*visited, neighbor], [*edges, edge]))
            bounded = bounded or bool(queue)
            paths.extend(found)
    relevant = {i for p in paths for i in p['relations']}
    relevant.update(i for i, r in enumerate(relations) if table_name in (r['table'], r['to_table']))
    ordered = sorted(relevant) + [i for i in range(len(relations)) if i not in relevant]
    return {'relations': [relations[i] for i in ordered], 'paths': [p['tables'] for p in paths],
            'ambiguous': any(sum(p['tables'][0] == a and p['tables'][-1] == b for p in paths) > 1
                             for a in hit for b in hit),
            'search_incomplete': bounded, 'total_relations': len(relations)}
