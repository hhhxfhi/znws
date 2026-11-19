"""0.7.0: finding the metrics a question is about, locally and deterministically.

1. Exact match: the metric's name (without a bracketed unit), aliases, indicator code or id
   appears in the question. These always come first.
2. Lexical score: BM25 over Chinese character pairs, fields weighted name 3, aliases 3,
   group 2, definition and dimensions 1.

The scorer is replaceable (rank_metrics(..., scorer=...)), so vector search can be added
later without touching the catalog.
"""
import re
import unicodedata

CODE = re.compile(r'[A-Z]{2,}_\d+(?:_\d+)*')
WEIGHTS = {'name': 3, 'aliases': 3, 'group': 2, 'definition': 1, 'dimensions': 1}


def normalize(text):
    return unicodedata.normalize('NFKC', str(text or '')).lower()


def strip_unit(name, unit=None):
    """Remove the registered measurement unit while preserving business qualifiers."""
    label = str(name or '').strip()
    suffix = re.search(r'[（(]([^）)]+)[）)]$', label)
    if unit and suffix and normalize(suffix[1]).strip() == normalize(unit).strip():
        return label[:suffix.start()].rstrip()
    return label


def code_of(m):
    if m.get('code'):
        return str(m['code'])
    found = CODE.search(str(m.get('definition') or ''))
    return found.group(0) if found else ''


def fields(m):
    dims = ' '.join(str(d.get('name') or d.get('id')) if isinstance(d, dict) else str(d) for d in m.get('dimensions') or [])
    return {'name': ' '.join(str(x) for x in (m.get('name'), m.get('label')) if x), 'aliases': ' '.join(map(str, m.get('aliases') or [])),
            'group': str(m.get('group') or ''), 'definition': str(m.get('definition') or ''), 'dimensions': dims}


def exact_hits(question, metrics):
    """Metrics the question names outright, in catalog order."""
    q = normalize(question)
    out = []
    for m in metrics:
        names = [strip_unit(m.get('name'), m.get('unit')), strip_unit(m.get('label'), m.get('unit')),
                 m.get('id'), code_of(m), *(m.get('aliases') or [])]
        if any(n and len(str(n)) >= 2 and normalize(n) in q for n in names):
            out.append(m)
    return out


class BM25:
    """Metric field adapter for the shared authorized-catalog scorer."""
    def __init__(self, metrics, k1=1.2, b=.75):
        from .retrieval import BM25Index
        self.index = BM25Index([[(text, WEIGHTS[field]) for field,text in fields(m).items()] for m in metrics],k1,b)

    def score(self, question, i):
        return self.index.score(question,i)


def bm25_scorer(question, metrics):
    index = BM25(metrics)
    return [index.score(question, i) for i in range(len(metrics))]


def rank_metrics(question, metrics, scorer=bm25_scorer):
    """Metrics in order of relevance: exact hits first, then by score, then catalog order.
    Without a question the catalog order is kept."""
    if not question:
        return list(metrics)
    hits = exact_hits(question, metrics)
    scores = scorer(question, metrics)
    rest = sorted((i for i, m in enumerate(metrics) if m not in hits), key=lambda i: (-scores[i], i))
    return hits + [metrics[i] for i in rest]
