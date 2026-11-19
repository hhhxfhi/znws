"""Host-owned capability settings. Registry membership never grants execution."""
import json
import threading
from pathlib import Path
from .store import ConflictError

def policy_key(scope):
 return '/'.join(scope.split('/')[:2]) if scope.startswith('business/') else scope

STATES={'enabled','history','disabled'}
class CapabilityPolicy:
 def __init__(self,path,definitions,skill_registry=lambda:(),scope_resolver=policy_key,skill_defaults=None):
  self.path=Path(path);self.definitions=definitions;self.skill_registry=skill_registry;self.lock=threading.RLock();self.scope_key=scope_resolver
  # 0.6.3: a capability or skill may default to off; explicit settings still win.
  self.skill_defaults=dict(skill_defaults or {})
 def config(self):
  with self.lock:
   return json.loads(self.path.read_text()) if self.path.exists() else {'schema_version':1,'revision':0,'global':{},'projects':{},'skills':{}}
 def snapshot(self,project):
  project=self.scope_key(project)
  cfg=self.config();states={}
  for name in self.definitions:
   local_cfg=cfg['projects'].get(project,{})
   global_state=cfg['global'].get(name,'enabled');local=local_cfg.get(name,'enabled')
   rank={'enabled':0,'history':1,'disabled':2}
   # The capability's default applies only when neither scope set it explicitly.
   states[name]=max([global_state,local],key=rank.get) if (name in cfg['global'] or name in local_cfg) else self.definitions[name].get('default','enabled')
  # Fixed point supports dependency chains regardless of registration order.
  for _ in self.definitions:
   previous=dict(states)
   for name,definition in self.definitions.items():
    for dependency in definition.get('requires',[]):
     states[name]=max([states[name],states.get(dependency,'disabled')],key=rank.get)
   if states==previous:break
  return {'revision':cfg['revision'],'states':states,'global':cfg['global'],'project':cfg['projects'].get(project,{}),'skills':cfg.get('skills',{}),'capabilities':[{'id':name,**d,'state':states[name]} for name,d in self.definitions.items()],'inflight_policy':'cancel database queries when data stops; running non-query operations settle; future execution rejected'}
 def update(self,project,changes,expected_revision,scope='project',skills=None):
  project=self.scope_key(project)
  if scope not in {'global','project'}:raise ValueError('未知配置范围')
  if any(name not in self.definitions or state not in STATES for name,state in changes.items()):raise ValueError('未知能力或状态')
  with self.lock:
   cfg=self.config()
   if cfg['revision']!=expected_revision:raise ConflictError('能力设置已变化，请刷新后重试。')
   target=cfg['global'] if scope=='global' else cfg['projects'].setdefault(project,{})
   target.update(changes)
   if skills is not None:
    if scope!='project':raise ValueError('技能开关需在业务项目配置')
    if not isinstance(skills,dict) or any(k not in self.skill_ids() or type(v) is not bool for k,v in skills.items()):raise ValueError('技能开关必须是布尔值')
    cfg.setdefault('skills',{}).setdefault(project,{}).update(skills)
   cfg['revision']+=1;self.path.parent.mkdir(parents=True,exist_ok=True)
   temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2));temp.replace(self.path)
   return self.snapshot(project)
 def require(self,project,capability,operation):
  state=self.snapshot(project)['states'].get(capability,'disabled')
  historical=operation in {'inspect','export','read'}
  if state=='disabled' or (state=='history' and not historical):raise PermissionError('CAPABILITY_DISABLED: 此业务项目已停止该能力的新执行。' if state=='history' else 'CAPABILITY_DISABLED: 此业务项目已禁用该能力。')
 def require_history(self,project):self.require(project,'data','inspect')
 def skill_ids(self):
  # Settings and execution share the same registered contracts.
  return {skill['id'] for skill in self.skill_registry()}
 def skill_enabled(self,project,skill):return self.config().get('skills',{}).get(self.scope_key(project),{}).get(skill,self.skill_defaults.get(skill,True))
