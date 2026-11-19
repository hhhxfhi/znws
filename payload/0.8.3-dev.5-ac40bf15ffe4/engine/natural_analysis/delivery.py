"""Quantitative delivery is a projection of evidence, never model arithmetic."""
from .claims import finalize

def compose(result):
 contract=result.get('metric_contract')
 if not contract or result.get('status')!='succeeded':return None
 claims=result.get('verified_claims',[])
 facts=result.get('facts')
 row_count=result.get('row_count')
 statements=[{'statement_id':f['id'],'fact_refs':[f['id']],'value':f['value'],'unit':contract['unit'],'dimensions':f.get('dimensions',{}),'text':c['verified_text']} for f,c in zip(result.get('facts',[]),finalize([{'fact':f} for f in result.get('facts',[])],contract))]
 statements += [{'statement_id':c['claim_id'],'fact_refs':c['fact_refs'],'text':c.get('public_text') or c['verified_text']} for c in claims if c.get('verified_text')]
 complete=(isinstance(facts,list) and type(row_count) is int and row_count>=0
          and not result.get('truncated',False) and len(facts)==row_count)
 return {'schema_version':1,'result_ref':result['result_ref'],'metric_id':contract['id'],'unit':contract['unit'],'periods':contract['periods'],'scope':contract.get('scope',{}),'formula':contract.get('formula'),'statements':statements,'limitations':list(dict.fromkeys(contract.get('limitations',[])+result.get('claim_limitations',[]))), 'complete':complete,'source':result.get('source_provenance',{}),'explanation_policy':'只描述证据支持的观察；未提供原因证据时不完成归因。'}
