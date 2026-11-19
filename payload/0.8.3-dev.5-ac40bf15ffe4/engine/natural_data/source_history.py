"""Read old receipts through an explicit migration map and current table grants.

Historical provenance stays immutable. These aliases are never live query sources.
"""
import copy

def history_source(engine, sources, source_id):
    direct=next((s for s in sources if s['id']==source_id),None)
    if direct is not None:return direct
    migration=engine.config().get('source_migrations',{}).get(source_id)
    if not migration:return None
    target=next((s for s in sources if s['id']==migration['source_id']),None)
    if not target or target.get('path')!=migration.get('target_path'):return None
    current={t['name']:t for t in target['tables']}
    tables=[{**copy.deepcopy(current[new]),'name':old} for old,new in migration['tables'].items() if new in current]
    return {**target,'id':source_id,'tables':tables}
