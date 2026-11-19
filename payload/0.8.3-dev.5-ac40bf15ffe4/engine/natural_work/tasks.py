"""Host-session scoped task memory. No domain rules and no agent loop."""
import uuid
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from .runtime import Capability,Operation

class Input(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
class Read(Input):pass
class FieldPatch(Input):
 name: str
 value: str
class Patch(Input):
 task_id:str|None=None
 expected_revision:int=Field(ge=0)
 action:Literal['start','refine','append','switch','resume','verify','cancel','provide_material']
 title:str=Field(min_length=1,max_length=300)
 capability_id:str=Field(min_length=1,max_length=80,description='问数任务固定填 "data"；不要填目录里的 metric_mapping')
 set_fields:list[FieldPatch]=Field(default_factory=list,description='字段补丁，每项{name,value}。保留未改字段，不把当前任务筛选传给新主题。')
 remove_fields:list[str]=Field(default_factory=list)
 state:Literal['needs_clarification','ready','running','partially_delivered','delivered','blocked','cancelled']='ready'
 result_refs:list[str]=Field(default_factory=list)
class Create(Input):
 expected_revision:int=Field(ge=0)
 action:Literal['start','append','switch']='append'
 title:str=Field(min_length=1,max_length=300)
 capability_id:str=Field(min_length=1,max_length=80,description='问数任务固定填 "data"；不要填目录里的 metric_mapping')
 set_fields:list[FieldPatch]=Field(default_factory=list)
 state:Literal['needs_clarification','ready','blocked']='ready'
class Update(Patch):
 title:str|None=None
 capability_id:str|None=None
 task_id:str=Field(min_length=1,description='work_read/create返回的原始task_id，必须填写')
 action:Literal['refine','resume','verify','cancel','provide_material']='refine'
class Output(Input):
 tasks:list[dict]
 revision:int
 note:str='任务状态是工作记忆，不能代替用户授权或业务正确性验证。'

def capability(store):
 def subject(ctx):
  if not ctx.session_id:raise ValueError('任务状态必须绑定宿主会话')
  return 'session:'+ctx.session_id
 def read(a,ctx):
  revision,head=store.head(ctx.project,subject(ctx),'work.tasks')
  return {'revision':revision,'tasks':head['payload']['tasks'] if head else []}
 def patch(a,ctx):
  current=read(a,ctx);tasks=current['tasks'];parents=[]
  if a.action in ['start','append','switch']:
   if a.task_id:raise ValueError('新任务不能复用已有task_id')
   if a.action=='switch':
    for t in tasks:
     if t['state'] not in ['delivered','cancelled']:t['state']='cancelled'
   task={'task_id':uuid.uuid4().hex,'title':a.title,'capability_id':a.capability_id,'fields':{},'state':a.state,'result_refs':[]}
   tasks.append(task)
  else:
   task=next((t for t in tasks if t['task_id']==a.task_id),None)
   if task is None:raise ValueError('任务不存在于当前会话')
   if task['capability_id']!=a.capability_id:raise ValueError('修改任务不能更换能力，请创建新任务')
  for field in a.remove_fields:task['fields'].pop(field,None)
  for patch in a.set_fields:
   task['fields'][patch.name]=patch.value
  task['revision']=current['revision']+1
  task['state']='cancelled' if a.action=='cancel' else a.state
  normalized=[]
  for ref in a.result_refs:
   try:artifact=store.get(ctx.project,ref)
   except PermissionError:
    # Query receipts expose both result_ref and artifact_ref. Adopt only an existing,
    # project-scoped dataset reference; never trust an unvalidated ID.
    ref='data:'+ref;artifact=store.get(ctx.project,ref)
   normalized.append(ref);parents.append(ref)
   if artifact['kind'] in ['work.tasks','data.catalog']:raise ValueError('任务或目录不是业务交付产物')
  task['result_refs']=list(dict.fromkeys(task['result_refs']+normalized))
  if task['state']=='delivered' and not task['result_refs']:raise ValueError('缺交付产物，不可声明已交付；澄清应为needs_clarification')
  _,revision=store.publish(ctx.project,subject(ctx),'work.tasks',{'tasks':tasks},expected_revision=a.expected_revision,producer='work.patch@1.0.0',parents=parents)
  # Echo the changed task in full; other tasks only as a short summary so the
  # receipt does not repeat every saved SQL on each update. work_read is full.
  brief=[t if t is task else {k:t.get(k) for k in ('task_id','title','state','revision')} for t in tasks]
  return {'tasks':brief,'revision':revision}
 def create(a,ctx):
  current=read(a,ctx)
  # Appending an independent task does not overwrite existing tasks. A switch
  # still requires a current snapshot because it cancels outstanding tasks.
  values=a.model_dump()
  if a.action in ['start','append']:values['expected_revision']=current['revision']
  return patch(Patch(**values),ctx)
 def update(a,ctx):
  current=read(a,ctx)
  task=next((t for t in current['tasks'] if t['task_id']==a.task_id),None)
  if task is None:raise ValueError('任务不存在于当前会话')
  if a.expected_revision < task.get('revision',current['revision']) or a.expected_revision>current['revision']:
   from .store import ConflictError
   raise ConflictError('任务已被修改，请work_read读取后重试；原条件保持不变。')
  return patch(Patch(**{**a.model_dump(),'title':a.title or task['title'],'capability_id':a.capability_id or task['capability_id'],'expected_revision':current['revision']}),ctx)
 return Capability('work','2.0.0','任务记忆',(
  Operation('read','读取会话任务','读取当前会话任务快照；用于多轮继承、回到历史、追加和取消。',Read,read,'work.read','work.tasks',Output),
  Operation('create','新建会话任务','新建独立任务；追加用append，切换主题用switch。返回task_id供后续计划和更新。',Create,create,'work.read','work.tasks',Output),
  Operation('update','修改已有任务','task_id必须原样填写。字段set/remove只修改明确指定条件，保留其他条件。',Update,update,'work.read','work.tasks',Output),
  Operation('patch','更新会话任务','保存明确任务及字段增删。材料不等于任务；不明确用needs_clarification。不启动业务查询；不构成权限或完成证明。',Patch,patch,'work.read','work.tasks',Output),
 ))
