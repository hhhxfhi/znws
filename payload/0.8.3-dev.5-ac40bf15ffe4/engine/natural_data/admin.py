"""Project configuration check before publishing (structure validation only)."""
import copy
from natural_work.store import ConflictError


class Administration:
    def __init__(self, engine, business):
        self.engine, self.business = engine, business

    def check_project(self, actor, args):
        with self.engine.lock:
            cfg=self.business.config()
            project=self.business.require(cfg,actor,args.get('business_project'),'editor')
            if args.get('expected_revision') != cfg['revision']:
                raise ConflictError('项目已修改，请刷新后校验。')
            draft=copy.deepcopy(args.get('draft',project['draft']))
            try:
                self.business.validate(draft,publish=True)
                errors=[]
            except ValueError as exc:
                errors=[{'message':str(exc),'action':'configuration'}]
            return {'valid':not errors,'errors':errors,'business_project':project['id'],
                    'revision':cfg['revision'],'published_version':project['published_version'],
                    'message':'结构校验通过；业务口径仍需业务负责人确认。' if not errors else '校验未通过，当前生效版本保持不变。'}
