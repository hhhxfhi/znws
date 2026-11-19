"""Versioned public corpus. Local snapshots never imply real-time legal coverage."""
import hashlib,json,re
from datetime import date
from pathlib import Path

class Corpus:
 def __init__(self,path):self.path=Path(path)
 def documents(self):
  raw=json.loads(self.path.read_text());docs=raw if isinstance(raw,list) else raw['documents']
  if len(docs)>10000:raise ValueError('知识库文档数量超过本地限制')
  keys=set()
  for d in docs:
   if not {'id','version','title','url','publisher','published_at','summary'}<=set(d):raise ValueError('语料缺少版本或出处字段')
   key=(d['id'],d['version'])
   if key in keys:raise ValueError('知识版本重复')
   keys.add(key)
   for field in ['published_at','effective_at','expires_at','verified_at']:
    if d.get(field):date.fromisoformat(d[field])
   if not re.match(r'^https://[^/]+/',d['url']):raise ValueError('知识来源必须是HTTPS页面')
  return docs
 def manifest(self):
  docs=self.documents();digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
  return {'schema_version':2,'corpus_version':digest[:16],'sha256':digest,'document_count':len(docs),'documents':[ {k:d[k] for k in ['id','version','title','url','publisher','published_at','effective_at','verified_at','jurisdiction','status_at_verification'] if k in d} for d in docs],'mode':'local_versioned_snapshot','limitations':['本地有限语料，不是实时联网检索或完整法规库。']}
 def search(self,query,as_of=None):
  day=date.fromisoformat(as_of) if as_of else date.today();tokens=[x for x in re.findall(r'[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}',query.lower())]
  hits=[]
  for d in self.documents():
   start=d.get('effective_at') or d['published_at'];end=d.get('expires_at')
   if start>day.isoformat() or (end and end<day.isoformat()):continue
   tags=d.get('tags',[]);score=sum(3 for t in tags if t in query)+sum(1 for t in tokens if t in (d['title']+' '+d['summary']).lower())
   if score:
    verified=d.get('verified_at');stale=not verified or (day-date.fromisoformat(verified)).days>180
    hits.append((score,{**d,'citation_id':d['id']+'@'+d['version'],'as_of':day.isoformat(),'verification_stale':stale,'verification_note':'需重新核验现行状态' if stale else '仅表示已核验来源页面；适用范围仍需核对'}))
  return [d for _,d in sorted(hits,key=lambda x:(-x[0],x[1]['id']))[:5]]
