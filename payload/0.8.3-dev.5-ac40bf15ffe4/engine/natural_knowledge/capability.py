import json
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
from natural_work.runtime import Capability,Operation
class Search(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 query:str=Field(min_length=2,max_length=1000)
 as_of:str|None=Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$',description='适用日期YYYY-MM-DD；不传使用当前日期。')
class Output(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 artifact_ref:str
 matches:list[dict]
 note:str
 corpus_version:str
 as_of:str
 limitations:list[str]

def capability(store,corpus_path=None):
 path=corpus_path or Path(__file__).resolve().parents[2]/'knowledge/public-policies.json'
 from .catalog import Corpus
 corpus=Corpus(path)
 def search(a,ctx):
  matches=corpus.search(a.query,a.as_of)
  manifest=corpus.manifest()
  value={'matches':matches,'note':'本地版本化知识快照；引用citation_id、来源URL和适用日期。未命中不表示没有相关政策。','corpus_version':manifest['corpus_version'],'as_of':a.as_of or __import__('datetime').date.today().isoformat(),'limitations':manifest['limitations']}
  ref=store.put(ctx.project,'knowledge.evidence',value,producer='knowledge.search@1.1.0')
  return {'artifact_ref':ref,**value}
 class Catalog(BaseModel):
  model_config=ConfigDict(extra='forbid',strict=True)
 class CatalogOutput(BaseModel):
  model_config=ConfigDict(extra='forbid',strict=True)
  corpus:dict
 return Capability('knowledge','1.1.0','知识检索',(
  Operation('catalog','查看知识范围','查看本地语料版本、出处与覆盖限制。',Catalog,lambda a,c:{'corpus':corpus.manifest()},'knowledge.read','knowledge.catalog',CatalogOutput),
  Operation('search','检索有出处的知识','查询本地版本化公开知识，返回来源URL、时间与摘要。知识咨询应使用此能力，不查业务数量。',Search,search,'knowledge.read','knowledge.evidence',Output),
 ))
