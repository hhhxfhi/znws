"""One lexical scorer for authorized catalog entries and their compact views.

Adapters select fields and exact identifiers; they do not implement new scorers.
Retrieval orders candidates, and never establishes business applicability.
"""
import math
import re
import unicodedata
from collections import Counter

STOP = {'多少', '一下', '什么', '请问', '是否', '怎么', '如何', '哪些', '分别',
        '以及', '另外', '查询', '查一', '统计', '帮我', '我想', '看看', '列出', '给我'}


def normalize(text):
    return unicodedata.normalize('NFKC', str(text or '')).casefold()


def tokens(text):
    text = normalize(text)
    result = re.findall(r'[a-z_][a-z_0-9]*|\d+', text)
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1:
            result.append(run)
    return [term for term in result if term not in STOP]


class BM25Index:
    def __init__(self, documents, k1=1.2, b=.75):
        """Each document contains (text, weight) pairs."""
        self.k1, self.b = k1, b
        self.docs = []
        for fields in documents:
            counts = Counter()
            for text, weight in fields:
                for term, count in Counter(tokens(text)).items():
                    counts[term] += count * weight
            self.docs.append(counts)
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.mean = sum(self.lengths) / max(1, len(self.docs))
        frequency = Counter(term for doc in self.docs for term in doc)
        self.idf = {term: math.log(1+(len(self.docs)-n+.5)/(n+.5)) for term,n in frequency.items()}

    def score(self, question, index):
        doc, length = self.docs[index], self.lengths[index]
        return sum(self.idf[term]*doc[term]*(self.k1+1) /
                   (doc[term]+self.k1*(1-self.b+self.b*length/max(1,self.mean)))
                   for term in set(tokens(question)) if doc[term])


def rank(items, question, fields, exact=None):
    if not question:
        return [(0, 0., item) for item in items]
    index = BM25Index([fields(item) for item in items])
    scored = [(int(exact(item, question)) if exact else 0, index.score(question,i),i,item)
              for i,item in enumerate(items)]
    return [(priority, score, item) for priority,score,_,item in
            sorted(scored,key=lambda x:(-x[0],-x[1],x[2]))]


def named(value, question):
    q = normalize(question)
    return any(isinstance(name,str) and len(name.strip())>=2 and normalize(name) in q
               for name in [value.get('name'),value.get('label'),*(value.get('aliases') or []),*(value.get('synonyms') or [])])
