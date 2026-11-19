/** 0.6.3 配置管理后台：独立管理界面。
 *
 * 列表只读、编辑在弹窗里，保存即生效（写草稿后立即发布）；批量导入等未生效的改动从顶栏
 * 「未生效的改动」统一处理。没有整页未保存状态，所以导航和项目切换从不被锁住。按钮、输入框、开关、复选框、菜单、弹窗、标签都用
 * Harness 原语；原语里没有表格和多行输入框，这两处用原生元素，只用 --dsw-alias-* 主题变量。
 */
export function createConsole(deps){
  const {React,h,UI,request,getJson,openSession,SettingsMenu,MetricBuilder,PackImport,TemplateTransfer,CapabilitySettings,DatabaseConnections,OnboardingPanel,currentWorkspace='',workspaces=[]}=deps;
  const {Button,Switch,Checkbox,Input,Modal,Tag}=UI;
  const icon=(name,size=16)=>UI[name]?h(UI[name],{size}):null;
  const GROUPS=['terms','examples','skills','layers','mappings'];
  const isMetric=row=>row?.config?.kind==='semantic-metric';
  const uid=()=>(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random().toString(16).slice(2));

  // ---------- small building blocks ----------
  function Field({label,hint,children}){return h('label',{className:'nwc-field'},h('span',{className:'nwc-field-label'},label),children,hint&&h('span',{className:'nwc-field-hint'},hint));}
  function Text({label,value,onChange,placeholder,disabled,hint,type}){return h(Field,{label,hint},h(Input,{'aria-label':label,value:value??'',placeholder,disabled,type,onChange:e=>onChange(e.target.value)}));}
  // No multi-line input in the Harness primitives; a native textarea with theme tokens.
  function Area({label,value,onChange,placeholder,disabled,rows=4,mono,hint}){return h(Field,{label,hint},h('textarea',{className:'nwc-textarea'+(mono?' nwc-mono':''),'aria-label':label,value:value??'',placeholder,disabled,rows,onChange:e=>onChange(e.target.value)}));}
  function optionLabel(text){const t=String(text??''),k=t.indexOf(' · ');return h('span',{className:'nwc-opt',title:t},h('span',{className:'nwc-opt-main'},k<0?t:t.slice(0,k)),k>=0&&h('span',{className:'nwc-opt-sub'},t.slice(k+3)));}
  function Pick({label,value,items,onChange,disabled,placeholder='请选择'}){
    const [open,setOpen]=React.useState(false),text=items.find(i=>i.id===value)?.label||value||placeholder;
    return h(Field,{label},h(UI.Menu,{open,portal:true,dense:true,className:'nwc-pick-anchor',listClassName:'nwc-pick-menu',onClose:()=>setOpen(false),selectedId:value,items:items.map(i=>({...i,label:optionLabel(i.label)})),onSelect:id=>{onChange(id);setOpen(false);},
      anchor:h(Button,{variant:'outline',size:'sm',disabled,className:'nwc-pick','aria-label':label,title:text,onClick:()=>setOpen(v=>!v)},h('span',{className:'nwc-ellipsis'},text),icon('IconChevronDownOutlineMedium'))}));
  }

  function Search({value,onChange,placeholder}){return h('div',{className:'nwc-search'},h(Input,{'aria-label':placeholder,placeholder,value,icon:icon('IconSearchOutlineMedium'),onChange:e=>onChange(e.target.value)}));}
  // Single-choice filter in a toolbar: one button, options in a menu.
  function FilterMenu({label,value,items,onChange}){const [open,setOpen]=React.useState(false),cur=items.find(i=>i.id===value);return h(UI.Menu,{open,portal:true,dense:true,selectedId:value,listClassName:'nwc-row-menu',onClose:()=>setOpen(false),items,onSelect:id=>{onChange(id);setOpen(false);},anchor:h(Button,{size:'md',variant:'outline','aria-label':label,onClick:()=>setOpen(v=>!v)},cur?.label||label,icon('IconChevronDownOutlineMedium',14))});}
  function Toolbar({children}){const list=React.Children.toArray(children),isButton=c=>c&&c.type===Button,end=list.filter(isButton);return h('div',{className:'nwc-toolbar'},list.filter(c=>!isButton(c)),end.length>0&&h('div',{className:'nwc-toolbar-end'},end.map(c=>React.cloneElement(c,{size:'md'}))));}
  function Card({title,actions,children}){return h('section',{className:'nwc-card'},(title||actions)&&h('div',{className:'nwc-card-head'},title&&h('h3',null,title),actions&&h('div',{className:'nwc-actions'},actions)),children);}
  // Name with trailing tags: only the name truncates, tags stay whole.
  function NameLine({text,children}){return h('div',{className:'nwc-strong nwc-name-line',title:text},h('span',{className:'nwc-name'},text),children);}
  function Empty({children}){return h('p',{className:'nwc-empty'},children);}
  // No table in the Harness primitives; a native table with theme tokens.
  // Columns drop out from the right (optional ones only) and the action column narrows until
  // every text column keeps at least FLEX_MIN px; below that the table scrolls with actions pinned.
  const FLEX_MIN=140,OPS_COMPACT=140,CELL_PAD=24;
  // Any cell whose text is cut off shows the full text on hover.
  const showFull=e=>{const el=e.currentTarget,cut=el.scrollWidth>el.clientWidth||[...el.querySelectorAll('*')].some(x=>x.scrollWidth>x.clientWidth);if(cut&&!el.title)el.title=el.innerText.replace(/\n+/g,'\n');};
  const Compact=React.createContext?React.createContext(false):null;
  function Table({columns,rows,rowKey,empty='暂无数据'}){
    const ref=React.useRef(null),[width,setWidth]=React.useState(0);
    React.useLayoutEffect(()=>{const el=ref.current;if(!el)return;setWidth(el.clientWidth);const ro=new ResizeObserver(([e])=>setWidth(e.contentRect.width));ro.observe(el);return()=>ro.disconnect();},[rows.length>0]);
    const need=(cols,compact,flex=FLEX_MIN)=>cols.reduce((n,c)=>n+CELL_PAD+(c.key==='ops'&&compact?Math.min(OPS_COMPACT,parseInt(c.width)||OPS_COMPACT):(parseInt(c.width)||flex)),0);
    let cols=columns,compact=false,flex=FLEX_MIN;
    if(width){const optional=columns.filter(c=>c.optional).reverse();for(const c of optional){if(need(cols,false)<=width)break;cols=cols.filter(x=>x!==c);}if(need(cols,false)>width)compact=true;
      // Still too wide: text columns give up width down to 80px each before the table scrolls.
      const flexCount=cols.filter(c=>!c.width).length;if(flexCount&&need(cols,compact)>width)flex=Math.max(80,Math.floor(FLEX_MIN-(need(cols,compact)-width)/flexCount));}
    if(!rows.length)return h(Empty,null,empty);
    const cls=c=>[c.className,c.key==='ops'&&'nwc-ops'].filter(Boolean).join(' ')||undefined;
    const wrap=child=>Compact?h(Compact.Provider,{value:compact},child):child;
    return wrap(h('div',{ref,className:'nwc-table-wrap'},h('table',{className:'nwc-table',style:{minWidth:need(cols,compact,flex)+'px'}},
      h('thead',null,h('tr',null,cols.map(c=>h('th',{key:c.key,className:cls(c),style:c.width?{width:c.key==='ops'&&compact?Math.min(OPS_COMPACT,parseInt(c.width))+'px':c.width}:undefined},c.title)))),
      h('tbody',null,rows.map((row,i)=>h('tr',{key:rowKey(row,i)},cols.map(c=>{const v=c.render?c.render(row):row[c.key];return h('td',{key:c.key,className:cls(c),title:typeof v==='string'?v:undefined},c.wrap?v:h('div',{className:'nwc-cell',onMouseEnter:showFull},v));})))))));
  }
  // Two visible actions per row; the rest (and every destructive one) go into 「更多」.
  function RowActions({items}){
    const [open,setOpen]=React.useState(false),compact=Compact?React.useContext(Compact):false;
    const list=items.filter(Boolean),safe=list.filter(i=>!i.danger),shown=safe.slice(0,compact?1:2),rest=[...safe.slice(shown.length),...list.filter(i=>i.danger)];
    const button=i=>h(Button,{key:i.label,size:'sm',variant:'ghost',disabled:i.disabled,onClick:i.onClick},i.label);
    if(rest.length===1&&shown.length<2)return h('div',{className:'nwc-row-actions'},shown.map(button),h(Button,{size:'sm',variant:'ghost',className:rest[0].danger?'nwc-danger-text':undefined,disabled:rest[0].disabled,onClick:rest[0].onClick},rest[0].label));
    return h('div',{className:'nwc-row-actions'},shown.map(button),rest.length>0&&h(UI.Menu,{open,portal:true,align:'end',dense:true,listClassName:'nwc-row-menu',onClose:()=>setOpen(false),items:rest.map(i=>({id:i.label,label:i.label,disabled:i.disabled,danger:i.danger})),onSelect:id=>{setOpen(false);rest.find(i=>i.label===id)?.onClick();},anchor:h(Button,{size:'sm',variant:'ghost','aria-label':'更多操作',onClick:()=>setOpen(v=>!v)},'更多',icon('IconChevronDownOutlineMedium',14))}));
  }
  function Dialog({open,title,description,onClose,children,footer,wide}){return h(Modal,{open,onClose,title,description,closeLabel:'关闭',footer,className:'nwc-dialog'+(wide?' nwc-dialog-wide':''),contentClassName:'nwc-dialog-content'},h('div',{className:'nwc-dialog-body'},children));}
  // Edit dialog: local copy, saved as one unit.
  function EditDialog({title,initial,onSave,onClose,render,wide,saveLabel='保存',validate,aside}){
    const [value,setValue]=React.useState(()=>structuredClone(initial)),[busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
    const set=patch=>setValue(v=>({...v,...(typeof patch==='function'?patch(v):patch)}));
    async function save(){const problem=validate?.(value);if(problem){setError(problem);return;}setBusy(true);setError('');try{await onSave(value);onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
    return h(Dialog,{open:true,title,onClose,wide,footer:h('div',{className:'nwc-dialog-footer'},h('div',{className:'nwc-actions nwc-footer-status'},aside?.(value,setError),error&&h('span',{role:'alert',className:'nwc-error-text nwc-ellipsis',title:error},error)),h('div',{className:'nwc-actions'},h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:onClose},'取消'),h(Button,{variant:'primary',size:'sm',disabled:busy,onClick:save},busy?'正在保存…':saveLabel)))},
      render(value,set,busy));
  }
  function Section({title,extra,children}){return h('section',{className:'nwc-form-section'},h('div',{className:'nwc-form-section-head'},h('h4',null,title),extra),children);}
  function Confirm({title,description,confirmLabel='确认',danger=true,onConfirm,onClose,children,blocked=false}){
    const [busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
    async function go(){setBusy(true);setError('');try{await onConfirm();onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
    return h(Dialog,{open:true,title,onClose,footer:h('div',{className:'nwc-actions'},h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:onClose},'取消'),h(Button,{variant:danger?'outline':'primary',size:'sm',className:danger?'nwc-danger-button':undefined,disabled:busy||blocked,onClick:go},busy?'正在处理…':confirmLabel))},
      description&&h('p',{className:'nwc-text'},description),children,error&&h('p',{role:'alert',className:'nwc-error'},error));
  }
  const clip=(text,n=80)=>{const s=String(text??'');return s.length>n?s.slice(0,n)+'…':s;};
  const time=at=>at?new Date(at*1000).toLocaleString():'';
  // Short time for table cells (full time on hover): 11-05 16:04, or 2025-11-05 in other years.
  const pad=n=>String(n).padStart(2,'0');
  const shortTime=at=>{if(!at)return '—';const d=new Date(at*1000),now=new Date();return h('span',{title:time(at)},d.getFullYear()===now.getFullYear()?pad(d.getMonth()+1)+'-'+pad(d.getDate())+' '+pad(d.getHours())+':'+pad(d.getMinutes()):d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate()));};

  // ---------- draft differences ----------
  function liveEntry(project){return project?.versions?.find(v=>v.version===project.published_version)||null;}
  function published(project){return liveEntry(project)?.config||null;}
  function draftOf(project){return project?(project.draft||published(project)):null;}
  const nameOf=(group,row)=>row?.name||row?.question||row?.id;
  const groupNames={terms:'术语与指标',examples:'查询示例',skills:'分析方法说明',layers:'空间图层说明',mappings:'字段映射'};
  function changes(project){
    const draft=draftOf(project),base=published(project)||{source_ids:[],tables:{},terms:[],examples:[],skills:[],layers:[],mappings:[]};
    if(!draft)return [];
    const out=[];
    const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
    for(const sid of new Set([...(draft.source_ids||[]),...(base.source_ids||[])])){
      const was=(base.source_ids||[]).includes(sid),is=(draft.source_ids||[]).includes(sid);
      if(was!==is)out.push({area:'数据范围',kind:is?'新增':'移除',name:'数据源 '+sid});
      else if(is&&!same([...(draft.tables?.[sid]||[])].sort(),[...(base.tables?.[sid]||[])].sort()))out.push({area:'数据范围',kind:'修改',name:'授权表（'+sid+'）'});
    }
    for(const group of GROUPS){
      const before=new Map((base[group]||[]).map(r=>[r.id,r])),after=new Map((draft[group]||[]).map(r=>[r.id,r]));
      for(const [id,row] of after){if(!before.has(id))out.push({area:groupNames[group],kind:'新增',name:nameOf(group,row)});else if(!same(row,before.get(id)))out.push({area:groupNames[group],kind:'修改',name:nameOf(group,row)});}
      for(const [id,row] of before)if(!after.has(id))out.push({area:groupNames[group],kind:'删除',name:nameOf(group,row)});
    }
    return out;
  }

  function ConfigurationDiagnostics({project,enabled}){
    const [open,setOpen]=React.useState(false),[value,setValue]=React.useState(null),[error,setError]=React.useState(''),[question,setQuestion]=React.useState(''),[busy,setBusy]=React.useState(false),[relation,setRelation]=React.useState(null);
    const run=async(action='diagnose',relation_id)=>{setBusy(true);setError('');try{const r=await request('admin_validation',{business_project:project.id,action,question,relation_id});action==='relation'?setRelation(r):setValue(r);}catch(e){setError(e.message);}finally{setBusy(false);}};
    if(!enabled)return null;
    const issues=Object.entries(value?.diagnostics||{}).flatMap(([source,rows])=>rows.map(r=>({...r,source})));
    return h(React.Fragment,null,h('div',{className:'nwc-actions'},h(Button,{variant:'outline',onClick:()=>{setOpen(true);run();}},'配置诊断')),
      open&&h(Dialog,{open:true,title:'配置诊断与复测',onClose:()=>setOpen(false)},
        h('p',null,'诊断当前已生效配置，不自动修改授权。关联诊断只读，单次最多 30 秒、返回 100 行，并发 1 次。'),
        error&&h('p',{role:'alert'},error),h(Text,{label:'代表问题：查看配置如何命中检索',value:question,onChange:setQuestion}),
        h(Button,{disabled:busy,onClick:()=>run()},busy?'检查中…':'检查配置与检索'),
        value&&h(React.Fragment,null,h('p',null,issues.length?'发现 '+issues.length+' 条待核对提示（显示前 100 条）。':'当前静态检查未发现问题，仍需实际查询与独立真值核对。'),
          issues.length>0&&h('ul',{className:'nwc-diagnostic-list'},issues.slice(0,100).map((r,i)=>h('li',{key:i},[r.name||r.table||r.column||'',r.message].filter(Boolean).join('：')))),
          ...Object.values(value.retrieval||{}).flat().map(r=>h('p',{key:r.entry_ref},r.summary+' · '+({explicit_name:'物理名称命中',business_name_or_alias:'业务名称或别名命中',bm25:'词法相关'}[r.retrieval_basis]||r.retrieval_basis))),
          ...(value.relations||[]).map(r=>h('div',{key:r.id,className:'nwc-actions'},h('span',null,r.name+' · '+r.keys.map(k=>k.join(' → ')).join('、')),h(Button,{disabled:busy,onClick:()=>run('relation',r.id)},'只读关联诊断'))),
          relation&&h('div',null,h(Tag,null,relation.state),h('p',null,relation.note),h('pre',null,JSON.stringify(relation.checks||relation.result,null,2))),
          h('p',null,'已有 '+(value.records||[]).length+' 条验证记录，其中 '+(value.records||[]).filter(r=>r.freshness==='expired').length+' 条已过期。可在查询示例中补独立预期、试运行或发起原生试问。'))));
  }

  // ---------- navigation ----------
  const NAV=[
    {group:'数据接入',items:[['connections','数据源连接','IconDatabaseOutlineMedium',{system:true}]]},
    {group:'业务项目',items:[['projects','项目列表','IconFolderOpenOutlineMedium'],['scope','数据范围',null,{project:true}],['members','成员与角色','IconUsersOutlineMedium',{project:true,admin:true}],['versions','变更历史',null,{project:true}]]},
    {group:'业务口径',items:[['mappings','术语与字段映射','IconListPenOutlineMedium',{project:true}],['metrics','指标口径',null,{project:true}],['examples','查询示例',null,{project:true}],['skills','分析技能',null,{project:true}],['imports','口径导入',null,{project:true,editor:true}],['excel','Excel 批量导入',null,{project:true,editor:true}]]},
    {group:'问数配置',items:[['capabilities','功能开关','IconSlidersTwoOutlineMedium',{project:true}]]},
  ];

  const flatIds=NAV.flatMap(g=>g.items.map(([id])=>id));
  function Console(props={}){
    const activeWorkspace=props?.currentWorkspace||currentWorkspace||'';
    const hostWorkspaces=Array.isArray(props?.workspaces)?props.workspaces:(Array.isArray(workspaces)?workspaces:[]);
    const [cfg,setCfg]=React.useState(null),[error,setError]=React.useState(''),[chosen,setRoute]=React.useState(''),[projectId,setProjectId]=React.useState(''),[chosenWorkspace,setChosenWorkspace]=React.useState(''),[notice,setNotice]=React.useState(null),[menu,setMenu]=React.useState(false),[pendingOpen,setPendingOpen]=React.useState(false);
    const [switchingProject,setSwitchingProject]=React.useState(false);
    const revision=React.useRef(0),readEpoch=React.useRef(0),selectedProject=React.useRef(''),selection=React.useRef({id:''});
    // Revision-gated reuse. The server alone decides whether a client may skip a payload; these
    // entries only retain bytes it previously sent for the same build, revisions and actor.
    const held=React.useRef({inventory:null,inventoryKey:'',details:new Map()});
    const CACHE_FIELDS=['draft','versions','review_candidates','review_reasons'];
    function rememberDetail(id,row){
      const details=held.current.details,fields={};
      for(const key of CACHE_FIELDS)if(key in row)fields[key]=structuredClone(row[key]);
      details.delete(id);details.set(id,{key:row.detail_revision||'',fields});
      // Switching between several already-open projects must not keep every catalogue in memory.
      while(details.size>4)details.delete(details.keys().next().value);
    }
    function reuseDetail(row){
      const details=held.current.details,entry=details.get(row.id);
      if(!entry||!entry.key||entry.key!==row.detail_revision)return false;
      Object.assign(row,structuredClone(entry.fields));
      details.delete(row.id);details.set(row.id,entry);
      return true;
    }
    async function reload(select,force){
      if(select!==undefined){
        selection.current={id:select};
        setSwitchingProject(select!==selectedProject.current);
      }
      const target=selection.current.id,epoch=++readEpoch.current;
      const cached=held.current,detail=target?cached.details.get(target):undefined;
      const args={view:'manager',business_project:target,project:activeWorkspace||'__operator__',
        ...(!force&&cached.inventoryKey?{cached_inventory:cached.inventoryKey}:{}),
        ...(!force&&detail?.key?{cached_detail:detail.key}:{})};
      let value;
      try{value=await request('config',args);}
      catch(e){
        if(epoch!==readEpoch.current)return false;
        selection.current.id=selectedProject.current;
        setSwitchingProject(false);
        throw e;
      }
      if(epoch!==readEpoch.current)return false;
      const row=(value.projects||[]).find(p=>p.id===value.selected_project);
      if(value.inventory_elided){
        if(!cached.inventory){
          if(force)throw Error('配置缓存已失效，请重新打开配置管理。');
          return reload(select,true);
        }
        value={...value,sources:cached.inventory};
      }else if(Array.isArray(value.sources)){cached.inventory=value.sources;cached.inventoryKey=value.inventory_revision||'';}
      if(row?.detail_reused){
        if(!reuseDetail(row)){
          if(force)throw Error('配置缓存已失效，请重新打开配置管理。');
          return reload(select,true);
        }
      }else if(row)rememberDetail(row.id,row);
      // A project this actor can no longer see must not stay renderable from an earlier response.
      for(const id of [...cached.details.keys()])if(!(value.projects||[]).some(p=>p.id===id))cached.details.delete(id);
      revision.current=value.revision;
      const want=value.selected_project||target||(activeWorkspace&&value.workspace_defaults?.[activeWorkspace])||value.default_project||value.projects[0]?.id||'';
      const id=value.projects.some(p=>p.id===want)?want:(value.projects[0]?.id||'');
      selection.current.id=id;
      selectedProject.current=id;
      setCfg(value);
      setProjectId(id);
      setSwitchingProject(false);
      return true;
    }
    React.useEffect(()=>{reload().catch(e=>setError(e.message));},[]);
    const project=cfg?.projects.find(p=>p.id===projectId)||null;
    const pending=React.useMemo(()=>project?changes(project):[],[project]);
    if(!cfg)return h('div',{className:'nwc'},h('div',{className:'nwc-loading'},h('p',{role:error?'alert':'status'},error||'正在读取配置…'),error&&h(Button,{size:'sm',variant:'outline',onClick:()=>{setError('');reload().catch(e=>setError(e.message));}},'重新读取')));
    // 进入配置管理默认是数据源连接；不能管理数据源的用户默认看项目列表。
    const route=flatIds.includes(chosen)?chosen:(cfg.system_admin?'connections':'projects');
    const role=project?.role,admin=role==='admin',editor=['admin','editor'].includes(role);
    const can=o=>!(o?.system&&!cfg.system_admin)&&!(o?.admin&&!admin)&&!(o?.editor&&!editor);
    const flat=NAV.flatMap(g=>g.items.map(([id,label,ic,o])=>({id,label,icon:ic,group:g.group,...(o||{})})));
    const current=flat.find(x=>x.id===route)||flat[0];
    const say=(text,failed=false)=>setNotice(text?{text,failed}:null);
    async function chooseProject(id){setPendingOpen(false);say('');return reload(id);}
    const viewSelection=selection.current;
    const isCurrentProject=()=>selection.current===viewSelection&&projectId===viewSelection.id;
    // Every project write: business_update, then reload; a conflict reloads and reports.
    async function act(action,args={},message){
      if(!isCurrentProject())throw Error('业务项目已切换，请重新打开该操作。');
      try{const v=await request('business_update',{action,business_project:projectId,expected_revision:revision.current,...args});await reload();if(message&&isCurrentProject())say(typeof message==='function'?message(v):message);return v;}
      catch(e){if(e.status===409){await reload();throw Error('配置已被其他人更新，已重新读取，请再试一次。');}throw e;}
    }
    // One revision-checked transaction; the server preserves any earlier pending draft.
    async function saveDraft(mutate,message='已保存并生效。'){
      const next=mutate(structuredClone(draftOf(project)));
      const value=await act('save',{draft:next,expected_revision:cfg.revision});
      if(!isCurrentProject())return;
      const saved=value.projects?.find(p=>p.id===projectId);
      say(saved&&changes(saved).length?'已保存到未生效的改动，请核对后统一应用。':message);
    }
    const ctx={cfg,project,admin,editor,act,saveDraft,reload:(select,force=false)=>reload(selection.current===viewSelection?select:undefined,force),say,go:setRoute,rev:()=>revision.current};
    const page=!project&&current.project&&route!=='projects'?h(Empty,null,cfg.projects.length?'请选择业务项目。':'还没有业务项目。'+(cfg.system_admin?'在「项目列表」中新建。':'')):
      route==='connections'?h(Connections,ctx):route==='projects'?h(Projects,{...ctx,chooseProject,pendingCount:pending.length,activeWorkspace,hostWorkspaces,chosenWorkspace,setChosenWorkspace}):route==='scope'?h(Scope,ctx):
      route==='members'?h(Members,ctx):route==='mappings'?h(Mappings,ctx):route==='metrics'?h(MetricPage,{...ctx,key:projectId,hasChanges:pending.length>0}):route==='examples'?h(Examples,ctx):route==='skills'?h(Skills,ctx):
      route==='imports'?h(Imports,{...ctx,key:projectId}):route==='excel'?h(ExcelImport,{...ctx,key:projectId}):route==='capabilities'?h(Capabilities,{...ctx,key:projectId}):
      route==='versions'?(switchingProject?h(Empty,null,'正在读取变更历史…'):h(Versions,{...ctx,key:projectId})):
      null;
    return h('div',{className:'nwc'},
      h('nav',{className:'nwc-nav','aria-label':'配置管理导航'},
        h('div',{className:'nwc-brand'},h('span',null,'配置管理'),cfg.plugin_version&&h('span',{className:'nwc-brand-version'},(cfg.edition?cfg.edition+' ':'')+cfg.plugin_version)),
        NAV.map(g=>{const items=g.items.filter(([,,,o])=>can(o));if(!items.length)return null;return h('div',{key:g.group,className:'nwc-nav-group'},h('div',{className:'nwc-nav-title'},g.group),
          items.map(([id,label,ic])=>h('button',{key:id,type:'button',className:'nwc-nav-item'+(id===route?' is-active':''),'aria-current':id===route?'page':undefined,onClick:()=>{setRoute(id);say('');}},h('span',{className:'nwc-nav-icon'},ic?icon(ic):null),h('span',null,label))));})),
      h('div',{className:'nwc-main'},
        h('header',{className:'nwc-top'},
          h('div',{className:'nwc-crumb'},h('span',null,current.group),h('span',null,'/'),h('h1',null,current.label)),
          h('div',{className:'nwc-actions'},
            current.project&&cfg.projects.length>0&&h(SettingsMenu,{open:menu,onClose:()=>setMenu(false),selectedId:projectId,items:cfg.projects.map(p=>({id:p.id,label:p.name})),onSelect:id=>{setMenu(false);chooseProject(id).catch(e=>say(e.message,true));},anchor:h(Button,{variant:'outline',size:'sm','aria-label':'切换业务项目',onClick:()=>setMenu(v=>!v)},icon('IconFolderOpenOutlineMedium'),h('span',{className:'nwc-ellipsis'},project?.name||'选择项目'),icon('IconChevronDownOutlineMedium'))}),
            current.project&&project&&!switchingProject&&(pending.length?h(Button,{size:'sm',variant:'outline',onClick:()=>setPendingOpen(true)},h(Tag,{tone:'warning'},pending.length+' 处'),'未生效的改动'):null))),
        notice&&h('div',{role:notice.failed?'alert':'status',className:'nwc-notice'+(notice.failed?' is-error':'')},h('span',null,notice.text),h(Button,{size:'sm',variant:'ghost',onClick:()=>say('')},'关闭')),
        h('main',{className:'nwc-content'},page),
        pendingOpen&&project&&!switchingProject&&h(Changes,{...ctx,key:projectId,pending,onClose:()=>{if(isCurrentProject())setPendingOpen(false);}})));
  }

  // ---------- pages ----------
  function Stat({label,value,action}){return h('button',{type:'button',className:'nwc-stat',disabled:!action,onClick:action||undefined},h('span',{className:'nwc-stat-value'},value),h('span',{className:'nwc-stat-label'},label));}

  // ----- 功能开关 -----
  const CAP_TEXT={work:'记录每次提问的任务、计划和结果；问数依赖它。',data:'在已授权的数据范围内查询数据并作答。',analysis:'在查询结果上做分布、对比、归因等分析。',knowledge:'检索知识文档。当前版本还没有这项能力，保持关闭。'};
  const CAP_STATE={enabled:['启用','success'],history:['仅查看历史','warning'],disabled:['关闭','quiet']};
  function StateMenu({value,disabled,label,onChange}){
    const [open,setOpen]=React.useState(false);
    return h(UI.Menu,{open,portal:true,align:'end',dense:true,listClassName:'nwc-row-menu',selectedId:value,onClose:()=>setOpen(false),items:Object.entries(CAP_STATE).map(([id,[l]])=>({id,label:l})),onSelect:id=>{setOpen(false);if(id!==value)onChange(id);},
      anchor:h(Button,{size:'sm',variant:'outline',disabled,'aria-label':label,onClick:()=>setOpen(v=>!v)},CAP_STATE[value]?.[0]||value,icon('IconChevronDownOutlineMedium',14))});
  }
  function Capabilities({cfg,project,admin,say}){
    const [scope,setScope]=React.useState('project'),[policy,setPolicy]=React.useState(null),[error,setError]=React.useState(''),[busy,setBusy]=React.useState(false);
    const load=()=>getJson('policy',{project:project.id}).then(v=>{setPolicy(v);setError('');}).catch(e=>setError(e.message));
    React.useEffect(()=>{load();},[]);
    if(!policy)return error?h(Card,null,h('p',{role:'alert',className:'nwc-error nwc-pad'},error),h('div',{className:'nwc-pad'},h(Button,{size:'sm',variant:'outline',onClick:load},'重新读取'))):h(Empty,null,'正在读取…');
    const editable=scope==='global'?cfg.system_admin:admin;
    const titleOf=id=>policy.capabilities.find(c=>c.id===id)?.title||id;
    async function change(id,state){setBusy(true);try{setPolicy(await request('policy_update',{project:project.id,business_project:project.id,scope,changes:{[id]:state},expected_revision:policy.revision}));say('已保存，'+(scope==='global'?'所有项目':'这个项目')+'的会话立即按新设置执行。');}catch(e){say(e.message,true);load();}finally{setBusy(false);}}
    return h(React.Fragment,null,
      h(Toolbar,null,h('div',{className:'nwc-pills'},h(UI.Pill,{active:scope==='project',onClick:()=>setScope('project')},'当前项目'),h(UI.Pill,{active:scope==='global',disabled:!cfg.system_admin,onClick:()=>cfg.system_admin&&setScope('global')},'全部项目')),
        h('span',{className:'nwc-sub'},scope==='global'?'对所有项目生效，优先于单个项目的设置。':'只对「'+project.name+'」生效；全部项目里关闭的功能这里不能打开。')),
      h(Card,null,h(Table,{rowKey:c=>c.id,rows:policy.capabilities,columns:[
        {key:'title',title:'功能',render:c=>h('div',null,h('div',{className:'nwc-strong'},c.title),h('div',{className:'nwc-sub',title:CAP_TEXT[c.id]||''},CAP_TEXT[c.id]||''))},
        {key:'requires',title:'依赖',optional:true,width:'90px',render:c=>c.requires.length?c.requires.map(titleOf).join('、'):'—'},
        {key:'state',title:'当前生效',width:'100px',render:c=>{const [l,t]=CAP_STATE[c.state]||[c.state,'neutral'];return h(Tag,{tone:t},l);}},
        {key:'ops',title:scope==='global'?'全部项目设置':'本项目设置',width:'150px',wrap:true,render:c=>h(StateMenu,{value:(scope==='global'?policy.global:policy.project)[c.id]||c.default||'enabled',disabled:busy||!editable,label:c.title+'设置',onChange:state=>change(c.id,state)})}]})));
  }

  // ----- 数据源连接 -----
  const SSL=[['verify-full','校验证书和主机名（推荐）'],['verify-ca','仅校验 CA'],['require','仅加密，不校验证书'],['prefer','允许回退明文'],['disable','不使用 SSL']].map(([id,label])=>({id,label}));
  const PG_LIKE=['postgresql','postgis','tbase','kingbase'];
  function Connections({cfg,project,reload,say}){
    const [cat,setCat]=React.useState(null),[error,setError]=React.useState(''),[q,setQ]=React.useState(''),[dialog,setDialog]=React.useState(null),[busy,setBusy]=React.useState(''),[tests,setTests]=React.useState({});
    async function load(){setCat(await request('connections'));}
    React.useEffect(()=>{load().catch(e=>setError(e.message));},[]);
    if(!cat)return error?h(Card,null,h('p',{role:'alert',className:'nwc-error nwc-pad'},error),h('div',{className:'nwc-pad'},h(Button,{size:'sm',variant:'outline',onClick:()=>{setError('');load().catch(e=>setError(e.message));}},'重新读取'))):h(Empty,null,'正在读取数据库连接…');
    const typeName=id=>cat.types.find(t=>t.id===id)?.label||id;
    const projectName=id=>cfg.projects.find(p=>p.id===id)?.name||id;
    async function update(args,message){
      try{const v=await request('connection_update',{...args,expected_revision:cat.revision});setCat(v);await reload();say(v?.message||message);return v;}
      catch(e){if(e.status===409){await load();throw Error('连接配置已被其他人更新，已重新读取，请再试一次。');}throw e;}
    }
    // Each row keeps its own last test result; the notice names the connection and the time.
    async function test(s){setBusy(s.id);setTests(t=>({...t,[s.id]:null}));const d=new Date(),at=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
      try{await request('connection_test',{source_id:s.id});setTests(t=>({...t,[s.id]:{ok:true,at}}));say('「'+s.name+'」连接成功（'+at+'）。');}
      catch(e){setTests(t=>({...t,[s.id]:{ok:false,at,text:e.message}}));say('「'+s.name+'」连接失败：'+e.message,true);}
      finally{setBusy('');}}
    const rows=cat.connections.filter(s=>!q||(s.name+' '+(s.description||'')+' '+s.dialect).toLowerCase().includes(q.toLowerCase()));
    const address=s=>s.dialect==='sqlite'?s.connection.path:(s.connection.host||'未配置')+(s.connection.port?':'+s.connection.port:'')+(s.connection.database?' / '+s.connection.database:'');
    const changed=s=>s.schema_changes&&Object.values(s.schema_changes).some(v=>v.length);
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索连接名称或类型'}),h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog({kind:'edit',isNew:true})},icon('IconPlusOutlineMedium'),'新增连接')),
      h(Card,null,h(Table,{rowKey:s=>s.id,rows,empty:cat.connections.length?'没有匹配的连接':'还没有数据库连接',columns:[
        {key:'name',title:'连接',render:s=>h('div',null,h(NameLine,{text:s.name},s.synthetic&&h(Tag,{tone:'neutral'},'模拟数据'),changed(s)&&h(Tag,{tone:'warning'},'结构有变化')),h('div',{className:'nwc-sub',title:typeName(s.dialect)+' · '+address(s)},typeName(s.dialect)+' · '+address(s)))},
        {key:'state',title:'状态',width:'110px',render:s=>{const t=tests[s.id];return h('div',null,s.enabled?h(Tag,{tone:'success'},'已启用'):h(Tag,{tone:'quiet'},'已停用'),busy===s.id?h('div',{className:'nwc-sub'},'正在测试…'):t&&h('div',{className:(t.ok?'nwc-ok-text':'nwc-error-text')+' nwc-sub',title:t.ok?'连接成功 '+t.at:t.text},(t.ok?'✓ 连接正常 ':'✗ 连接失败 ')+t.at.slice(0,5)));}},
        {key:'tables',title:'表',optional:true,width:'70px',render:s=>String(s.table_count)},
        {key:'used',title:'关联项目',optional:true,width:'100px',render:s=>s.used_by.length?h('span',{title:s.used_by.map(projectName).join('、')},s.used_by.length+' 个'):'—'},
        {key:'checked',title:'最近检查',optional:true,width:'110px',render:s=>shortTime(s.last_checked)},
        {key:'ops',title:'操作',width:'200px',wrap:true,render:s=>h(RowActions,{items:[
          {label:'编辑',onClick:()=>setDialog({kind:'edit',source:s})},
          {label:busy===s.id?'正在测试…':'测试连接',disabled:Boolean(busy),onClick:()=>test(s)},
          {label:'查看表结构',onClick:()=>setDialog({kind:'tables',source:s})},
          {label:'刷新表结构',onClick:()=>update({action:'refresh',source_id:s.id},'表结构已更新，请检查相关项目的数据范围。').catch(e=>say(e.message,true))},
          s.enabled?{label:'停用',onClick:()=>setDialog({kind:'disable',source:s})}:{label:'启用',onClick:()=>update({action:'state',source_id:s.id,enabled:true},'连接已启用。').catch(e=>say(e.message,true))},
          {label:'删除',danger:true,onClick:()=>setDialog({kind:'delete',source:s})}]})}]})),
      dialog?.kind==='edit'&&h(ConnectionDialog,{cat,source:dialog.source,isNew:dialog.isNew,update,say,onClose:()=>setDialog(null)}),
      dialog?.kind==='tables'&&h(SchemaDialog,{source:dialog.source,project,onClose:()=>setDialog(null),onRefresh:()=>update({action:'refresh',source_id:dialog.source.id},'表结构已更新，请检查相关项目的数据范围。').then(v=>setDialog({kind:'tables',source:v.connections.find(x=>x.id===dialog.source.id)||dialog.source}))}),
      dialog?.kind==='disable'&&h(Confirm,{title:'停用连接「'+dialog.source.name+'」',description:'停用后不再发起新查询，正在执行的查询会被取消；项目配置和历史结果保留，可随时重新启用。',confirmLabel:'停用连接',onClose:()=>setDialog(null),onConfirm:()=>update({action:'state',source_id:dialog.source.id,enabled:false},'连接已停用。')}),
      dialog?.kind==='delete'&&h(DeleteConnection,{source:dialog.source,projectName,onClose:()=>setDialog(null),onConfirm:detach=>update({action:'delete',source_id:dialog.source.id,confirm_delete:true,confirm_detach:detach,expected_business_revision:cat.business_revision},'连接已删除，源数据库没有改动。')}));
  }
  function DeleteConnection({source,projectName,onClose,onConfirm}){
    const [detach,setDetach]=React.useState(false),used=source.used_by.length>0;
    return h(Confirm,{title:'删除连接「'+source.name+'」',description:'删除连接配置和保存的凭据，不删除源数据库中的任何表或数据。',confirmLabel:'删除连接',blocked:used&&!detach,onClose,onConfirm:()=>onConfirm(detach)},
      used&&h(Checkbox,{label:'同时从 '+source.used_by.length+' 个项目中移除这个数据源（'+source.used_by.map(projectName).join('、')+'），立即撤销查询授权',checked:detach,onChange:setDetach}));
  }
  function ConnectionDialog({cat,source,isNew,update,say,onClose}){
    const [testing,setTesting]=React.useState(false),[tested,setTested]=React.useState(null);
    const initial=isNew?{name:'',description:'',dialect:'postgresql',host:'',port:5432,database:'',schema:'public',username:'',password:'',sslmode:'verify-full',ssl_ca:'',path:'',synthetic:false,clear_password:false,confirm_target_change:false}
      :{name:source.name,description:source.description||'',dialect:source.dialect,...source.connection,password:'',clear_password:false,synthetic:Boolean(source.synthetic),confirm_target_change:false};
    const targetKeys=v=>['dialect',...(v.dialect==='sqlite'?['path']:['host','port','database','schema','username','sslmode','ssl_ca'])];
    const targetChanged=v=>!isNew&&targetKeys(v).some(k=>String(v[k]??'')!==String((k==='dialect'?source.dialect:source.connection[k])??''));
    const onlyText=v=>!isNew&&!targetChanged(v)&&!v.password&&!v.clear_password&&Boolean(v.synthetic)===Boolean(source.synthetic);
    function payload(v){const {password_set,tls_warning,clear_password,confirm_target_change,...values}=v;values.port=Number(values.port);values.password_action=!isNew&&source.connection.password_set&&!values.password&&!clear_password?'keep':'replace';if(clear_password)values.password='';return {source_id:isNew?undefined:source.id,connection:values,confirm_target_change};}
    const dbLabel=d=>d==='oracle'?'Oracle 服务名':d==='dameng'?'达梦实例':'数据库名称';
    return h(EditDialog,{title:isNew?'新增数据库连接':'编辑连接「'+source.name+'」',wide:true,initial,saveLabel:isNew?'保存并读取表结构':'保存',onClose,
      validate:v=>!v.name.trim()?'请填写连接名称。':v.dialect==='sqlite'?(!v.path.trim()&&'请填写数据库文件路径。'):(!v.host.trim()||!v.port)?'请填写服务器地址和端口。':targetChanged(v)&&source.used_by.length&&!v.confirm_target_change?'这个连接已被项目使用，修改连接目标前请勾选确认。':null,
      onSave:v=>onlyText(v)?update({action:'metadata',source_id:source.id,name:v.name.trim(),description:v.description},'名称和说明已保存。'):update({action:'save',...payload(v)},isNew?'连接已保存，表结构已读取。请到「数据范围」为项目授权表。':'连接已保存，表结构已重新读取。'),
      aside:(v,setError)=>h(React.Fragment,null,h(Button,{size:'sm',variant:'outline',disabled:testing,onClick:async()=>{setTesting(true);setTested(null);setError('');try{const r=await request('connection_test',payload(v));setTested({ok:true,text:'连接成功'});}catch(e){setTested({ok:false,text:e.message||'连接失败'});}finally{setTesting(false);}}},testing?'正在测试…':'测试连接'),
        tested&&h('span',{role:tested.ok?'status':'alert',className:(tested.ok?'nwc-ok-text':'nwc-error-text')+' nwc-ellipsis',title:tested.text},tested.ok?'✓ '+tested.text:'连接失败：'+tested.text)),
      render:(v,set)=>h(React.Fragment,null,
        h(Section,{title:'基本信息'},h('div',{className:'nwc-form-grid'},
          h(Text,{label:'连接名称',value:v.name,onChange:name=>set({name})}),
          h(Pick,{label:'数据库类型',value:v.dialect,items:cat.types.map(t=>({id:t.id,label:t.label+(t.available?'':'（驱动未安装）'),disabled:!t.available})),onChange:id=>set(x=>({dialect:id,port:cat.types.find(t=>t.id===id)?.port||5432,schema:id==='mysql'?x.database:'public',password:''}))})),
          h(Text,{label:'说明',value:v.description,placeholder:'可选，例如用途、负责人',onChange:description=>set({description})})),
        h(Section,{title:'连接参数'},v.dialect==='sqlite'?h(Text,{label:'数据库文件路径',value:v.path,placeholder:'已有数据库文件的绝对路径',onChange:path=>set({path})}):h(React.Fragment,null,
          h('div',{className:'nwc-form-grid nwc-form-grid-host'},
            h(Text,{label:'服务器地址',value:v.host,onChange:host=>set({host})}),
            h(Text,{label:'端口',value:String(v.port??''),type:'number',onChange:port=>set({port})})),
          h('div',{className:'nwc-form-grid'},
            h(Text,{label:dbLabel(v.dialect),value:v.database,onChange:database=>set({database})}),
            h(Text,{label:'Schema',value:v.schema,placeholder:v.dialect==='mysql'?'与数据库名称一致':'public',onChange:schema=>set({schema})}),
            h(Text,{label:'用户名',value:v.username,onChange:username=>set({username})}),
            h(Field,{label:'密码'},h(Input,{'aria-label':'密码',type:'password',autoComplete:'new-password',value:v.password,disabled:v.clear_password,placeholder:!isNew&&source.connection.password_set?'留空则保留已保存的密码':'',onChange:e=>set({password:e.target.value})}))),
          !isNew&&source.connection.password_set&&h(Checkbox,{label:'清空已保存的密码',checked:v.clear_password,onChange:clear_password=>set({clear_password})}),
          [...PG_LIKE,'mysql'].includes(v.dialect)?h(React.Fragment,null,h('div',{className:'nwc-form-grid'},h(Pick,{label:'SSL 模式',value:v.sslmode,items:SSL.filter(x=>v.dialect!=='mysql'||x.id!=='prefer'),onChange:sslmode=>set({sslmode})}),h(Text,{label:'SSL CA 证书路径（可选）',value:v.ssl_ca,placeholder:'MySQL 留空使用系统 CA；PostgreSQL 使用驱动默认 CA',onChange:ssl_ca=>set({ssl_ca})})),v.sslmode!=='verify-full'&&h('p',{role:'alert',className:'nwc-sub'},'当前模式未同时强制加密、校验证书和主机名；仅在已核实的受控环境中使用。')):h('p',{className:'nwc-sub'},'这类数据库的加密连接需按部署说明单独配置。'))),
        h(Section,{title:'其他'},
          h(Checkbox,{label:'这是测试或模拟数据',checked:v.synthetic,onChange:synthetic=>set({synthetic})}),
          targetChanged(v)&&source.used_by.length>0&&h(Checkbox,{label:'确认把使用这个连接的 '+source.used_by.length+' 个项目切换到新的数据库目标（之后需重新核对授权表和口径）',checked:v.confirm_target_change,onChange:confirm_target_change=>set({confirm_target_change})}),
          h('p',{className:'nwc-sub'},'保存时会测试连接并读取表结构，不会写入源数据库。只改名称和说明时不重新连接。')))});
  }
  function SchemaDialog({source,project,onClose,onRefresh}){
    // Business table names usually live in a project's 表业务名 mappings, not on the connection.
    const config=project&&(published(project)||draftOf(project));
    const mapped=new Map((config?.mappings||[]).filter(m=>m.kind==='table'&&m.source_id===source.id&&m.name).map(m=>[m.table,m.name]));
    const label=t=>mapped.get(t.name)||t.label||'';
    const [q,setQ]=React.useState(''),[busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
    const rows=source.tables.filter(t=>!q||(t.name+' '+label(t)+' '+t.columns.map(c=>c.name).join(' ')).toLowerCase().includes(q.toLowerCase()));
    const ch=source.schema_changes||{};
    // Tables in one schema: show the schema once in the header, not on every row.
    const schemas=new Set(source.tables.map(t=>t.name.includes('.')?t.name.slice(0,t.name.indexOf('.')+1):''));
    const prefix=schemas.size===1?[...schemas][0]:'',short=n=>prefix&&n.startsWith(prefix)?n.slice(prefix.length):n;
    return h(Dialog,{open:true,wide:true,title:'表结构 · '+source.name,onClose,footer:h('div',{className:'nwc-dialog-footer'},h('span',{className:'nwc-sub'},'共 '+source.table_count+' 张表'+(source.last_checked?' · 最近检查 '+time(source.last_checked):'')+(mapped.size?' · 业务名取自项目「'+project.name+'」的表业务名':'')),h('div',{className:'nwc-actions'},h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:async()=>{setBusy(true);setError('');try{await onRefresh();}catch(e){setError(e.message);}finally{setBusy(false);}}},busy?'正在刷新…':'刷新表结构'),h(Button,{size:'sm',variant:'primary',onClick:onClose},'关闭')))},
      Object.values(ch).some(v=>v?.length)&&h('div',{className:'nwc-notice is-warning'},'最近一次刷新：新增 '+(ch.added_tables||[]).length+' 张表，移除 '+(ch.removed_tables||[]).length+' 张表，'+(ch.changed_tables||[]).length+' 张表字段有变化。请核对项目的授权表和指标口径。'),
      h(Search,{value:q,onChange:setQ,placeholder:'搜索表名、业务名或字段'}),
      h(Table,{rowKey:t=>t.name,rows,empty:'没有匹配的表',columns:[
        {key:'name',title:prefix?'表名（'+prefix.slice(0,-1)+'）':'表名',render:t=>h('span',{className:'nwc-mono',title:t.name},short(t.name))},
        {key:'label',title:'业务名',render:t=>h('span',{title:label(t)},label(t)||'—')},
        {key:'count',title:'字段数',width:'70px',render:t=>String(t.columns.length)},
        {key:'columns',title:'字段',render:t=>{const text=t.columns.map(c=>c.name).join('、');return h('span',{className:'nwc-mono',title:t.columns.map(c=>c.name+' '+c.type).join('\n')},text);}}]}),
      error&&h('p',{role:'alert',className:'nwc-error'},error));
  }

  function Projects({cfg,project,chooseProject,pendingCount,say,go,reload,activeWorkspace,hostWorkspaces=[],chosenWorkspace='',setChosenWorkspace}){
    const [dialog,setDialog]=React.useState(null),[q,setQ]=React.useState(''),[wsMenu,setWsMenu]=React.useState(false);
    const rows=cfg.projects.filter(p=>!q||p.name.includes(q)||(p.description||'').includes(q));
    const roles={admin:'项目管理员',editor:'编辑者',viewer:'使用者'};
    const targetWorkspace=chosenWorkspace||activeWorkspace;
    const isHostWs=Boolean(targetWorkspace&&targetWorkspace===activeWorkspace);
    const wsOptions=[];const seenWs=new Set();
    const addWs=(path,label)=>{const p=String(path||'').trim();if(!p||seenWs.has(p))return;seenWs.add(p);wsOptions.push({id:p,label});};
    const labelForWs=(path,suffix='')=>{const hw=hostWorkspaces.find(w=>w.path===path);return (hw?.title||path.split('/').pop()||path)+suffix+' · '+path;};
    if(activeWorkspace)addWs(activeWorkspace,labelForWs(activeWorkspace,'（当前会话）'));
    for(const w of hostWorkspaces)addWs(w.path,labelForWs(w.path));
    for(const p of Object.keys(cfg.workspace_defaults||{}))addWs(p,labelForWs(p,'（已配置）'));
    const targetLabel=wsOptions.find(o=>o.id===targetWorkspace)?.label.split(' · ')[0]||(targetWorkspace?targetWorkspace.split('/').pop()||targetWorkspace:'选择工作区…');
    // Project-level actions target the chosen row, not the project picked in the top bar.
    async function rowAct(p,action,args,message){
      try{const v=await request('business_update',{action,business_project:p.id,expected_revision:cfg.revision,...args});await reload(v?.selected_project||p.id);if(message)say(message);}catch(e){if(e.status===409)await reload();throw e;}
    }
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索项目'}),
        SettingsMenu&&wsOptions.length>0&&h(SettingsMenu,{open:wsMenu,onClose:()=>setWsMenu(false),selectedId:targetWorkspace,items:[...wsOptions,...(chosenWorkspace&&chosenWorkspace!==activeWorkspace?[{id:'',label:activeWorkspace?'切回当前会话工作区':'取消工作区筛选'}]:[])],onSelect:id=>{setChosenWorkspace?.(id);setWsMenu(false);say('');},anchor:h(Button,{variant:'outline',size:'sm','aria-label':'选择工作区',onClick:()=>setWsMenu(v=>!v)},icon('IconFolderOpenOutlineMedium'),h('span',{className:'nwc-ellipsis'},'工作区：'+targetLabel),icon('IconChevronDownOutlineMedium'))}),
        cfg.system_admin&&h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog({kind:'create'})},icon('IconPlusOutlineMedium'),'新建项目')),
      h(Card,null,h(Table,{rowKey:p=>p.id,rows,empty:'没有业务项目',columns:[
        {key:'name',title:'项目',render:p=>{
          const isGlobal=cfg.default_project===p.id;
          const isWs=targetWorkspace&&cfg.workspace_defaults?.[targetWorkspace]===p.id;
          const wsCount=Object.values(cfg.workspace_defaults||{}).filter(bid=>bid===p.id).length;
          return h('div',null,
            h(NameLine,{text:p.name},
              isGlobal&&h(Tag,{tone:'info'},'全局默认'),
              isWs&&h(Tag,{tone:'success'},isHostWs?'当前工作区默认':'所选工作区默认'),
              (!isWs&&wsCount>0)&&h(Tag,{tone:'neutral'},wsCount+' 个工作区默认')
            ),
            p.description&&h('div',{className:'nwc-sub',title:p.description},clip(p.description,60))
          );
        }},
        {key:'state',title:'配置状态',width:'150px',render:p=>{const n=p.id===project?.id?pendingCount:(p.details_loaded===false?p.pending_change_count:changes(p).length);return h('span',null,h(Tag,{tone:p.published_version?'success':'neutral'},p.published_version?'已生效':'尚未生效'),n>0&&h(Tag,{tone:'warning'},n+' 处未应用'));}},
        {key:'updated',title:'最近生效时间',width:'150px',render:p=>shortTime(liveEntry(p)?.at)},
        {key:'sources',title:'数据源 / 表',optional:true,width:'100px',render:p=>{const d=draftOf(p)||{source_ids:[],tables:{}};return d.source_ids.length+' / '+Object.values(d.tables||{}).reduce((n,t)=>n+t.length,0);}},
        {key:'role',title:'我的角色',optional:true,width:'100px',render:p=>roles[p.role]||p.role||'—'},
        {key:'ops',title:'操作',width:'220px',wrap:true,render:p=>h(RowActions,{items:[
          {label:'进入',onClick:()=>chooseProject(p.id).then(loaded=>{if(loaded)go('scope');}).catch(e=>say(e.message,true))},
          p.role==='admin'&&{label:'编辑信息',onClick:()=>setDialog({kind:'edit',project:p})},
          cfg.default_project!==p.id&&{label:'设为全局默认',disabled:!p.published_version,onClick:()=>rowAct(p,'default',{},'已设为新会话的全局默认项目。').catch(e=>say(e.message,true))},
          targetWorkspace?(
            cfg.workspace_defaults?.[targetWorkspace]===p.id?
              {label:'清除工作区默认',disabled:!p.published_version,onClick:()=>rowAct(p,'workspace_default',{workspace:targetWorkspace,clear:true},'已清除该工作区的默认项目设置。').catch(e=>say(e.message,true))}:
              {label:isHostWs?'设为当前工作区默认':'设为所选工作区默认',disabled:!p.published_version,onClick:()=>rowAct(p,'workspace_default',{workspace:targetWorkspace,business_project:p.id},'已设为该工作区的默认项目。').catch(e=>say(e.message,true))}
          ):{label:'设置工作区默认…',disabled:!p.published_version,onClick:()=>setDialog({kind:'workspace_default',project:p})},
          p.role==='admin'&&{label:'删除',danger:true,onClick:()=>setDialog({kind:'delete',project:p})}
        ]})}]})),
      (cfg.archived_projects||[]).length>0&&h(Card,{title:'已删除项目'},h(Table,{rowKey:p=>p.id,rows:cfg.archived_projects,columns:[{key:'name',title:'项目'},{key:'ops',title:'操作',width:'160px',wrap:true,render:p=>h(RowActions,{items:[{label:'恢复为草稿',onClick:()=>rowAct({id:p.id},'restore',{business_project:p.id},'项目已恢复为草稿，请核对数据范围后发布。').catch(e=>say(e.message,true))}]})}]})),
      dialog?.kind==='create'&&h(EditDialog,{title:'新建业务项目',initial:{name:''},validate:v=>!v.name.trim()&&'请填写项目名称。',onClose:()=>setDialog(null),onSave:async v=>{const r=await request('business_update',{action:'create',expected_revision:cfg.revision,name:v.name.trim()});await reload(r?.selected_project);say('项目已创建，请配置数据范围后发布。');go('scope');},render:(v,set)=>h(Text,{label:'项目名称',value:v.name,onChange:name=>set({name})})}),
      dialog?.kind==='edit'&&h(EditDialog,{title:'编辑项目信息',initial:{name:dialog.project.name,description:dialog.project.description||''},validate:v=>!v.name.trim()&&'请填写项目名称。',onClose:()=>setDialog(null),onSave:v=>rowAct(dialog.project,'metadata',{name:v.name.trim(),description:v.description},'项目信息已保存。'),render:(v,set)=>h(React.Fragment,null,h(Text,{label:'项目名称',value:v.name,onChange:name=>set({name})}),h(Area,{label:'项目说明',value:v.description,rows:3,onChange:description=>set({description})}))}),
      dialog?.kind==='workspace_default'&&h(EditDialog,{
        title:'设置「'+dialog.project.name+'」的工作区默认',
        initial:{workspace:targetWorkspace||''},
        validate:v=>!v.workspace.trim()&&'请填写工作区路径。',
        onClose:()=>setDialog(null),
        onSave:v=>rowAct(dialog.project,'workspace_default',{workspace:v.workspace.trim(),business_project:dialog.project.id},'已将项目绑定为该工作区的默认项目。'),
        render:(v,set)=>h(React.Fragment,null,
          wsOptions.length>0&&h(Pick,{label:'选择已有工作区',value:wsOptions.some(o=>o.id===v.workspace)?v.workspace:'',items:[{id:'',label:'手动输入路径…'},...wsOptions],onChange:id=>{if(id)set({workspace:id});}}),
          h(Text,{label:'工作区路径',value:v.workspace,placeholder:'/path/to/workspace',onChange:workspace=>set({workspace})}),
          Object.entries(cfg.workspace_defaults||{}).filter(([,bid])=>bid===dialog.project.id).map(([ws])=>h('div',{key:ws,className:'nwc-actions nwc-pad'},h('span',{className:'nwc-sub'},'已绑定：'+ws),h(Button,{size:'sm',variant:'outline',onClick:()=>{rowAct(dialog.project,'workspace_default',{workspace:ws,clear:true},'已清除工作区「'+ws+'」的默认设置。').then(()=>setDialog(null)).catch(e=>say(e.message,true));}},'清除此绑定'))),
          h('p',{className:'nwc-sub nwc-pad'},'设置后，在该工作区中新建会话将优先使用此业务项目。不覆盖全局默认，不基于历史会话自动推断。')
        )
      }),
      dialog?.kind==='delete'&&h(Confirm,{title:'删除项目「'+dialog.project.name+'」',description:'删除后停止新查询并清除默认选择；历史结果保留，可在「已删除项目」中恢复为草稿。',confirmLabel:'删除项目',onClose:()=>setDialog(null),onConfirm:()=>rowAct(dialog.project,'delete',{confirm_delete:true},'项目已删除。')}));
  }

  function Scope({cfg,project,admin,saveDraft,act,say,go}){
    const draft=draftOf(project),[dialog,setDialog]=React.useState(null),[q,setQ]=React.useState('');
    const rows=cfg.sources.filter(s=>!q||s.name.includes(q));
    function bind(s,on){return saveDraft(d=>({...d,source_ids:on?[...d.source_ids,s.id]:d.source_ids.filter(id=>id!==s.id),tables:on?{...d.tables,[s.id]:s.tables.map(t=>t.name)}:Object.fromEntries(Object.entries(d.tables).filter(([id])=>id!==s.id)),examples:on?d.examples:d.examples.filter(r=>r.source_id!==s.id),terms:on?d.terms:d.terms.filter(r=>(r.config?.source_id||r.source_id)!==s.id),mappings:on?(d.mappings||[]):(d.mappings||[]).filter(r=>r.source_id!==s.id)}),on?'数据源已接入，可以查询。':'数据源已移除，查询授权已撤销。');}
    return h(React.Fragment,null,
      h(ConfigurationDiagnostics,{project:project,enabled:admin}),
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索数据源'}),cfg.system_admin&&h(Button,{size:'sm',variant:'outline',onClick:()=>go('connections')},'管理数据源连接')),
      h(Card,null,h(Table,{rowKey:s=>s.id,rows,empty:'还没有可用的数据源',columns:[
        {key:'name',title:'数据源',render:s=>h('div',null,h(NameLine,{text:s.name},s.synthetic&&h(Tag,{tone:'neutral'},'模拟数据')),h('div',{className:'nwc-sub'},s.dialect+(s.enabled===false?' · 连接已停用':'')))},
        {key:'state',title:'状态',optional:true,width:'100px',render:s=>draft.source_ids.includes(s.id)?h(Tag,{tone:'success'},'已接入'):h(Tag,{tone:'quiet'},'未接入')},
        {key:'tables',title:'授权表',width:'120px',render:s=>draft.source_ids.includes(s.id)?(draft.tables[s.id]||[]).length+' / '+s.table_count:'—'},
        {key:'ops',title:'操作',width:'220px',wrap:true,render:s=>{const on=draft.source_ids.includes(s.id);return h(RowActions,{items:[
          on&&{label:admin?'选择授权表':'查看授权表',onClick:()=>setDialog({kind:'tables',source:s})},
          on&&{label:'测试连接',onClick:()=>request('test',{business_project:project.id,source_id:s.id}).then(v=>say(v?.message||'连接测试完成。')).catch(e=>say(e.message,true))},
          admin&&!on&&{label:'接入',onClick:()=>bind(s,true).catch(e=>say(e.message,true))},
          admin&&on&&{label:'移除',danger:true,onClick:()=>setDialog({kind:'remove',source:s})}]});}}]})),
      dialog?.kind==='tables'&&h(TablePicker,{source:dialog.source,selected:draft.tables[dialog.source.id]||[],editable:admin,onClose:()=>setDialog(null),onSave:names=>saveDraft(d=>({...d,tables:{...d.tables,[dialog.source.id]:names}}),'授权表已保存到草稿。')}),
      dialog?.kind==='remove'&&h(Confirm,{title:'移除数据源「'+dialog.source.name+'」',description:'同时移除该数据源的授权表、字段映射、查询示例和结构化指标，立即撤销查询授权；历史结果保留。',confirmLabel:'移除数据源',onClose:()=>setDialog(null),onConfirm:()=>bind(dialog.source,false)}));
  }
  function TablePicker({source,selected,editable,onSave,onClose}){
    const [picked,setPicked]=React.useState(()=>new Set(selected)),[q,setQ]=React.useState('');
    const all=source.tables.map(t=>t.name),missing=selected.filter(n=>!all.includes(n));
    const shown=source.tables.filter(t=>!q||t.name.toLowerCase().includes(q.toLowerCase())||(t.label||'').includes(q));
    const toggle=(name,on)=>setPicked(p=>{const n=new Set(p);on?n.add(name):n.delete(name);return n;});
    return h(EditDialog,{title:'授权表 · '+source.name,initial:{},wide:true,saveLabel:'保存授权表',onClose,onSave:()=>{if(!editable)return;return onSave([...picked].filter(n=>all.includes(n)||missing.includes(n)));},render:()=>h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索表名或说明'}),h('span',{className:'nwc-sub'},'已选 '+[...picked].filter(n=>all.includes(n)).length+' / '+all.length),editable&&h(Button,{size:'sm',variant:'ghost',onClick:()=>setPicked(p=>new Set([...p,...shown.map(t=>t.name)]))},'全选'),editable&&h(Button,{size:'sm',variant:'ghost',onClick:()=>setPicked(p=>{const n=new Set(p);shown.forEach(t=>n.delete(t.name));return n;})},'全不选')),
      missing.length>0&&h('p',{className:'nwc-error'},'以下表在源库中已不存在，保存时会保留勾选，请取消授权：'+missing.join('、')),
      h('div',{className:'nwc-check-list'},shown.map(t=>h('div',{key:t.name,className:'nwc-check-row',title:t.label||t.name},h(Checkbox,{label:t.name,checked:picked.has(t.name),disabled:!editable,onChange:on=>toggle(t.name,on)}),t.label&&h('span',{className:'nwc-sub nwc-ellipsis'},t.label)))))});
  }

  function Members({project,act}){
    const [dialog,setDialog]=React.useState(null);
    const labels={viewer:'使用者（可咨询）',editor:'编辑者（可修改配置）',admin:'项目管理员'};
    const rows=Object.entries(project.members||{}).map(([user,role])=>({user,role}));
    const admins=rows.filter(r=>r.role==='admin').length;
    return h(React.Fragment,null,
      h(Toolbar,null,h('span',{className:'nwc-sub'},'使用账户系统提供的身份标识授权，添加标识不会创建登录账户。'),h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog({user:'',role:'viewer',isNew:true})},icon('IconPlusOutlineMedium'),'添加成员')),
      h(Card,null,h(Table,{rowKey:r=>r.user,rows,empty:'还没有成员',columns:[{key:'user',title:'身份标识'},{key:'role',title:'角色',width:'140px',render:r=>labels[r.role]||r.role},
        {key:'ops',title:'操作',width:'200px',wrap:true,render:r=>h(RowActions,{items:[{label:'修改角色',onClick:()=>setDialog({...r})},{label:'移除',danger:true,disabled:r.role==='admin'&&admins===1,onClick:()=>setDialog({...r,remove:true})}]})}]})),
      dialog&&!dialog.remove&&h(EditDialog,{title:dialog.isNew?'添加成员':'修改角色',initial:dialog,validate:v=>!v.user.trim()&&'请填写身份标识。',onClose:()=>setDialog(null),onSave:v=>act('member',{user:v.user.trim(),role:v.role},'成员授权已保存。'),render:(v,set)=>h(React.Fragment,null,h(Text,{label:'身份标识',value:v.user,disabled:!dialog.isNew,placeholder:'例如 local:用户名',onChange:user=>set({user})}),h(Pick,{label:'角色',value:v.role,items:Object.entries(labels).map(([id,label])=>({id,label})),onChange:role=>set({role})}))}),
      dialog?.remove&&h(Confirm,{title:'移除成员「'+dialog.user+'」',description:'移除后该身份不能再使用此项目。',confirmLabel:'移除成员',onClose:()=>setDialog(null),onConfirm:()=>act('member',{user:dialog.user,role:'remove'},'成员已移除。')}));
  }

  // ----- 术语与字段映射 -----
  const KINDS=[['table','表业务名'],['field','字段业务名'],['term','术语与同义词'],['enum','枚举对照'],['time','时间语义'],['relation','表关系']];
  const kindLabel=k=>KINDS.find(x=>x[0]===k)?.[1]||k;
  function Mappings(props){
    const [tab,setTab]=React.useState('mappings');
    return h(React.Fragment,null,
      h(ConfigurationDiagnostics,{project:props.project,enabled:props.editor}),
      h('div',{className:'nwc-tabs'},h(UI.SegmentedControl,{value:tab,label:'术语与字段映射',options:[{value:'mappings',label:'字段映射'},{value:'terms',label:'业务术语'}],onChange:setTab})),
      tab==='mappings'?h(MappingList,props):h(TermList,props));
  }
  function MappingList({cfg,project,editor,saveDraft,say}){
    const draft=draftOf(project),[q,setQ]=React.useState(''),[kind,setKind]=React.useState('all'),[dialog,setDialog]=React.useState(null);
    const sources=cfg.sources.filter(s=>draft.source_ids.includes(s.id));
    const tablesOf=sid=>(sources.find(s=>s.id===sid)?.tables||[]).filter(t=>(draft.tables[sid]||[]).includes(t.name));
    const sourceName=sid=>sources.find(s=>s.id===sid)?.name||sid;
    const rows=(draft.mappings||[]).filter(r=>(kind==='all'||r.kind===kind)&&(!q||[r.name,r.description,r.table,r.column,...(r.synonyms||[])].join(' ').toLowerCase().includes(q.toLowerCase())));
    const short=t=>String(t||'').split('.').pop();
    const place=r=>r.kind==='relation'?short(r.table)+'('+(r.from_columns||[]).join(',')+') → '+short(r.to_table)+'('+(r.to_columns||[]).join(',')+')':short(r.table)+(r.column?'.'+r.column:'');
    const detail=r=>r.kind==='enum'?(r.values||[]).map(v=>v.value+'='+v.label).join('，'):r.kind==='time'?({date:'日',month:'月',year:'年',datetime:'日期时间'}[r.grain]||r.grain):(r.synonyms||[]).join('，');
    function blank(){const s=sources[0],t=s&&tablesOf(s.id)[0];return {id:uid(),kind:'field',name:'',description:'',source_id:s?.id||'',table:t?.name||'',column:'',synonyms:[],values:[],grain:'date',to_table:'',from_columns:[],to_columns:[],cardinality:'many_to_one'};}
    function clean(v){const base={id:v.id,kind:v.kind,name:v.name.trim(),description:v.description||'',source_id:v.source_id,table:v.table};
      if(!['table','relation'].includes(v.kind))base.column=v.column;
      if(['table','field','term'].includes(v.kind))base.synonyms=v.synonyms||[];
      if(v.kind==='enum')base.values=(v.values||[]).filter(x=>String(x.value).trim()!==''||x.label).map(x=>({value:x.value,label:x.label,...(x.type&&x.type!=='text'?{type:x.type}:{})}));
      if(v.kind==='time')base.grain=v.grain;
      if(v.kind==='relation')Object.assign(base,{to_table:v.to_table,from_columns:v.from_columns,to_columns:v.to_columns,cardinality:v.cardinality});
      return base;}
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索名称、同义词、表或字段'}),
        h(FilterMenu,{label:'类型',value:kind,onChange:setKind,items:[['all','全部类型'],...KINDS].map(([id,label])=>({id,label:label+'（'+(id==='all'?(draft.mappings||[]).length:(draft.mappings||[]).filter(r=>r.kind===id).length)+'）'}))}),
        editor&&h(Button,{size:'sm',variant:'primary',disabled:!sources.length,onClick:()=>setDialog({row:blank(),isNew:true})},icon('IconPlusOutlineMedium'),'新增映射')),
      h(Card,null,h(Table,{rowKey:r=>r.id,rows,empty:(draft.mappings||[]).length?'没有匹配的映射':'还没有字段映射',columns:[
        {key:'kind',title:'类型',optional:true,width:'110px',render:r=>h(Tag,{tone:'neutral'},kindLabel(r.kind))},
        {key:'name',title:'业务名称',render:r=>h('div',null,h('div',{className:'nwc-strong',title:r.name},r.name||'未命名'),r.description&&h('div',{className:'nwc-sub',title:r.description},r.description))},
        {key:'place',title:'对应位置',render:r=>h('span',{className:'nwc-mono',title:sourceName(r.source_id)+' · '+r.table+(r.column?'.'+r.column:'')},place(r))},
        {key:'detail',title:'同义词 / 取值',optional:true,render:r=>h('span',{title:detail(r)},detail(r)||'—')},
        {key:'ops',title:'操作',width:'150px',wrap:true,render:r=>h(RowActions,{items:[{label:editor?'编辑':'查看',onClick:()=>setDialog({row:r})},editor&&{label:'删除',danger:true,onClick:()=>setDialog({row:r,remove:true})}]})}]})),
      dialog&&!dialog.remove&&h(EditDialog,{title:(dialog.isNew?'新增':editor?'编辑':'查看')+'映射',initial:{...blank(),...dialog.row},wide:true,saveLabel:editor?'保存':'关闭',
        validate:v=>!editor?null:!v.name.trim()?'请填写业务名称。':!v.table?'请选择物理表。':(!['table','relation'].includes(v.kind)&&!v.column)?'请选择物理字段。':(v.kind==='relation'&&(!v.to_table||!v.from_columns.length||v.from_columns.length!==v.to_columns.length))?'请填写关联目标表和等长的关联字段。':null,
        onClose:()=>setDialog(null),onSave:v=>{if(!editor)return;const row=clean(v);return saveDraft(d=>({...d,mappings:dialog.isNew?[...(d.mappings||[]),row]:(d.mappings||[]).map(r=>r.id===row.id?row:r)}));},
        render:(v,set)=>h(MappingForm,{v,set,sources,tablesOf,disabled:!editor,isNew:dialog.isNew})}),
      dialog?.remove&&h(Confirm,{title:'删除映射「'+(dialog.row.name||'未命名')+'」',description:'删除后对话不再使用这条映射。',confirmLabel:'删除映射',onClose:()=>setDialog(null),onConfirm:()=>saveDraft(d=>({...d,mappings:(d.mappings||[]).filter(r=>r.id!==dialog.row.id)}),'映射已删除。')}));
  }
  function MappingForm({v,set,sources,tablesOf,disabled,isNew}){
    const tables=tablesOf(v.source_id),table=tables.find(t=>t.name===v.table),columns=table?.columns||[];
    const tableItems=tables.map(t=>({id:t.name,label:t.name+(t.label?' · '+t.label:'')})),columnItems=columns.map(c=>({id:c.name,label:c.name+(c.label&&c.label!==c.name?' · '+c.label:'')}));
    const list=s=>String(s||'').split(/[,，、\s]+/).map(x=>x.trim()).filter(Boolean);
    return h('div',{className:'nwc-form-grid'},
      h(Pick,{label:'映射类型',value:v.kind,disabled:disabled||!isNew,items:KINDS.map(([id,label])=>({id,label})),onChange:kind=>set({kind})}),
      h(Text,{label:'业务名称',value:v.name,disabled,placeholder:'业务人员使用的叫法',onChange:name=>set({name})}),
      h(Pick,{label:'数据源',value:v.source_id,disabled,items:sources.map(s=>({id:s.id,label:s.name})),onChange:source_id=>set({source_id,table:'',column:'',to_table:'',from_columns:[],to_columns:[]})}),
      h(Pick,{label:'物理表',value:v.table,disabled,items:tableItems,placeholder:tables.length?'选择表':'该数据源没有授权表',onChange:t=>set({table:t,column:'',from_columns:[]})}),
      !['table','relation'].includes(v.kind)&&h(Pick,{label:'物理字段',value:v.column,disabled:disabled||!table,items:columnItems,placeholder:table?'选择字段':'先选择表',onChange:column=>set({column})}),
      ['table','field','term'].includes(v.kind)&&h(Text,{label:'同义词',value:(v.synonyms||[]).join('，'),disabled,placeholder:'多个叫法用逗号分隔',onChange:s=>set({synonyms:list(s)})}),
      v.kind==='time'&&h(Pick,{label:'时间粒度',value:v.grain,disabled,items:[{id:'date',label:'日'},{id:'month',label:'月'},{id:'year',label:'年'},{id:'datetime',label:'日期时间'}],onChange:grain=>set({grain})}),
      v.kind==='relation'&&h(React.Fragment,null,
        h(Pick,{label:'关联目标表',value:v.to_table,disabled,items:tableItems,onChange:to_table=>set({to_table,to_columns:[]})}),
        h(Text,{label:'本表关联字段',value:(v.from_columns||[]).join(','),disabled,placeholder:'复合键用逗号分隔',onChange:s=>set({from_columns:list(s)})}),
        h(Text,{label:'目标表关联字段',value:(v.to_columns||[]).join(','),disabled,placeholder:'与本表字段一一对应',onChange:s=>set({to_columns:list(s)})}),
        h(Pick,{label:'对应关系',value:v.cardinality,disabled,items:[{id:'many_to_one',label:'多对一'},{id:'one_to_one',label:'一对一'},{id:'one_to_many',label:'一对多'}],onChange:cardinality=>set({cardinality})})),
      h('div',{className:'nwc-span'},h(Area,{label:'说明',value:v.description,disabled,rows:2,onChange:description=>set({description})})),
      v.kind==='enum'&&h('div',{className:'nwc-span'},h(EnumEditor,{values:v.values||[],disabled,onChange:values=>set({values})})));
  }
  function EnumEditor({values,onChange,disabled}){
    const change=(i,patch)=>onChange(values.map((x,j)=>j===i?{...x,...patch}:x));
    return h('div',{className:'nwc-enum'},h('div',{className:'nwc-field-label'},'取值对照（原始值 → 业务名称）'),
      values.map((x,i)=>h('div',{key:i,className:'nwc-enum-row'},h(Input,{'aria-label':'原始值 '+(i+1),value:String(x.value??''),disabled,placeholder:'原始值',onChange:e=>change(i,{value:e.target.value})}),h('span',null,'→'),h(Input,{'aria-label':'业务名称 '+(i+1),value:x.label||'',disabled,placeholder:'业务名称',onChange:e=>change(i,{label:e.target.value})}),!disabled&&h(Button,{size:'sm',variant:'ghost',onClick:()=>onChange(values.filter((_,j)=>j!==i))},'删除'))),
      !disabled&&h(Button,{size:'sm',variant:'outline',onClick:()=>onChange([...values,{value:'',label:''}])},icon('IconPlusOutlineMedium'),'添加取值'));
  }
  function TermList({cfg,project,editor,saveDraft}){
    const draft=draftOf(project),[q,setQ]=React.useState(''),[dialog,setDialog]=React.useState(null);
    const projectSources=cfg.sources.filter(s=>draft.source_ids.includes(s.id)),scopeName=id=>id?(cfg.sources.find(s=>s.id===id)?.name||id):'全部数据库';
    const terms=draft.terms.filter(r=>!isMetric(r)&&!project.review_reasons?.[r.id]&&r.status!=='review'),rows=terms.filter(r=>!q||(r.name+' '+r.definition).includes(q));
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索术语'}),editor&&h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog({row:{id:uid(),name:'',definition:''},isNew:true})},icon('IconPlusOutlineMedium'),'新增术语')),
      h(Card,null,h(Table,{rowKey:r=>r.id,rows,empty:terms.length?'没有匹配的术语':'还没有业务术语',columns:[{key:'name',title:'术语',width:'200px',render:r=>h('span',{className:'nwc-strong',title:r.name},r.name||'未命名')},{key:'scope',title:'适用数据库',optional:true,width:'150px',render:r=>h('span',{title:scopeName(r.source_id)},scopeName(r.source_id))},{key:'definition',title:'定义与口径',render:r=>h('span',{title:r.definition},r.definition)},
        {key:'ops',title:'操作',width:'150px',wrap:true,render:r=>h(RowActions,{items:[{label:editor?'编辑':'查看',onClick:()=>setDialog({row:r})},editor&&{label:'删除',danger:true,onClick:()=>setDialog({row:r,remove:true})}]})}]})),
      dialog&&!dialog.remove&&h(EditDialog,{title:(dialog.isNew?'新增':'编辑')+'术语',initial:dialog.row,wide:true,saveLabel:editor?'保存':'关闭',validate:v=>editor&&!v.name.trim()&&'请填写术语名称。',onClose:()=>setDialog(null),
        onSave:v=>{if(!editor)return;const row={...dialog.row,name:v.name.trim(),definition:v.definition,source_id:v.source_id||''};if(!row.source_id)delete row.source_id;return saveDraft(d=>({...d,terms:dialog.isNew?[...d.terms,row]:d.terms.map(r=>r.id===row.id?row:r)}));},
        render:(v,set)=>h(React.Fragment,null,h('div',{className:'nwc-form-grid'},h(Text,{label:'术语',value:v.name,disabled:!editor,onChange:name=>set({name})}),h(Pick,{label:'适用数据库',value:v.source_id||'',disabled:!editor,items:[{id:'',label:'全部数据库 · 各库口径相同时选这个'},...projectSources.map(s=>({id:s.id,label:s.name+' · 只用于这个库'}))],onChange:source_id=>set({source_id})})),h(Area,{label:'定义与口径',value:v.definition,disabled:!editor,rows:6,placeholder:'含义、单位、计算方式、时间规则',onChange:definition=>set({definition})}))}),
      dialog?.remove&&h(Confirm,{title:'删除术语「'+dialog.row.name+'」',confirmLabel:'删除术语',onClose:()=>setDialog(null),onConfirm:()=>saveDraft(d=>({...d,terms:d.terms.filter(r=>r.id!==dialog.row.id)}),'术语已删除。')}));
  }

  // ----- 指标口径 -----
  // A patch revision can update one term while retaining its existing workbook example.
  const workbookOrigin=/^pack:chdl-2026@2\.1\.\d+$/;
  const workbookCodePattern=/^([A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*)[\s:：]+/i;
  const workbookSourcePattern=/\r?\n[^\S\r\n]*唯一来源[：:]([^\r\n]+)\s*$/u;
  const workbookStates={
    example:['有查询示例','info'],
    needs_history:['缺少可比历史','quiet'],
    no_example:['示例未导入','quiet'],
    not_listed:['当前配置未收录','quiet'],
  };

  function workbookEntries(config){
    const entries=new Map(),sourceIds=new Set(config?.source_ids||[]);
    const codeFor=(row,title)=>String(row.metric_code||row.code||title?.match(workbookCodePattern)?.[1]||'').trim().toUpperCase();
    const keyFor=(row,code)=>row.source_id+'|'+code;
    for(const term of config?.terms||[]){
      if(!workbookOrigin.test(term.origin)||!sourceIds.has(term.source_id))continue;
      const code=codeFor(term,term.name);
      if(!code)continue;
      const key=keyFor(term,code);
      entries.set(key,{key,code,term,sourceId:term.source_id,example:null});
    }
    for(const example of config?.examples||[]){
      if(!workbookOrigin.test(example.origin)||!example.logical_sql?.trim())continue;
      const entry=entries.get(keyFor(example,codeFor(example,example.question)));
      if(entry)entry.example=example;
    }
    return entries;
  }

  function workbookMetricRows(project,scope){
    const selected=scope==='draft'?project.draft:published(project);
    const current=workbookEntries(selected);
    const known=new Map([...workbookEntries(published(project)),...workbookEntries(project.draft)]);
    return [...known.values()].map(original=>{
      const entry=current.get(original.key);
      const {key,code,term,sourceId}=entry||original;
      const limitation=entry?.term.config?.kind==='metric-limitation'?entry.term.config:null;
      const state=!entry?'not_listed':limitation?'needs_history':entry.example?'example':'no_example';
      const storedDefinition=entry?.term.definition;
      const source=storedDefinition?.match(workbookSourcePattern);
      const definition=(source?storedDefinition.slice(0,source.index):storedDefinition)?.trim();
      return {key,code,sourceId,state,name:term.name.replace(workbookCodePattern,''),definition,provenance:source?.[1].trim(),
        unit:limitation?.unit||definition?.match(/^单位[：:]\s*([^。；\n]+)/)?.[1],
        reason:limitation?.reason,example:state==='example'?entry.example:null};
    });
  }

  function WorkbookMetrics({cfg,project,scope,hasChanges}){
    const [query,setQuery]=React.useState(''),[opened,setOpened]=React.useState(null);
    const rows=workbookMetricRows(project,scope);
    const keyword=query.trim().toLocaleLowerCase();
    const filtered=rows.filter(row=>[row.code,row.name].some(value=>value.toLocaleLowerCase().includes(keyword)));
    const detail=filtered.find(row=>row.key===opened);
    const sourceName=id=>cfg.sources.find(source=>source.id===id)?.name||id;
    const counts=Object.fromEntries(Object.keys(workbookStates).map(state=>[state,rows.filter(row=>row.state===state).length]));
    const summary=Object.entries(workbookStates).filter(([state])=>counts[state]).map(([state,[label]])=>label+' '+counts[state]+' 项').join(' · ');
    const scopeLabel=scope==='draft'?(hasChanges?'草稿 · 未生效的改动须应用':'草稿 · 与已发布配置一致'):
      project.published_version?'已发布配置 · 第 '+project.published_version+' 版':'尚未发布';
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:query,onChange:setQuery,placeholder:'搜索指标编码或名称'})),
      h(Card,{title:'内置指标'},
        h('p',{className:'nwc-sub nwc-pad'},scopeLabel+'。'+summary+'。查询时请核对统计期间和数据源。'),
        h(Table,{rowKey:row=>row.key,rows:filtered,empty:'没有匹配的内置指标',columns:[
          {key:'name',title:'指标',render:row=>h('div',null,h(NameLine,{text:row.name}),h('div',{className:'nwc-sub'},row.code))},
          {key:'source',title:'数据源',optional:true,width:'150px',render:row=>sourceName(row.sourceId)},
          {key:'state',title:'状态',width:'124px',render:row=>h(Tag,{tone:workbookStates[row.state][1]},workbookStates[row.state][0])},
          {key:'ops',title:'操作',width:'110px',wrap:true,render:row=>h(RowActions,{items:[{label:'查看口径',onClick:()=>setOpened(row.key)}]})}
        ]})),
      detail&&h(Dialog,{open:true,title:detail.code+' · '+detail.name,wide:true,onClose:()=>setOpened(null)},
        h('p',{className:'nwc-sub'},sourceName(detail.sourceId)+(detail.unit?' · 单位 '+detail.unit:'')),
        detail.definition&&h(Section,{title:'业务定义'},h('p',{className:'nwc-text'},detail.definition)),
        detail.state==='needs_history'&&h('p',{className:'nwc-text'},detail.reason+'补齐同口径历史后再核算。'),
        detail.state==='no_example'&&h('p',{className:'nwc-text'},'此数据源的当前配置未导入匹配的 SQL 示例。可在「口径导入」核对数据源与方言。'),
        detail.state==='not_listed'&&h('p',{className:'nwc-text'},'所选配置未收录这项内置指标。'),
        detail.provenance&&h(Section,{title:'工作簿来源'},h('p',{className:'nwc-text'},detail.provenance)),
        detail.example&&h(Area,{label:'只读 SQL 示例',value:detail.example.logical_sql,disabled:true,rows:10,mono:true})));
  }

  function MetricPage(props){
    const {project,hasChanges}=props;
    const live=published(project);
    const hasWorkbook=workbookEntries(project.draft).size>0||workbookEntries(live).size>0;
    const [tab,setTab]=React.useState('workbook'),[selectedScope,setScope]=React.useState(null);
    if(!hasWorkbook)return h(Metrics,props);
    const scope=project.draft?(selectedScope||(hasChanges?'draft':'published')):'published';
    const metricCount=config=>'（'+(config?.terms||[]).filter(isMetric).length+'）';
    const views=[{value:'draft',label:'当前草稿'+(tab==='structured'?metricCount(project.draft):'')},
      {value:'published',label:'已发布配置'+(tab==='structured'?metricCount(live):'')}];
    const body=tab==='workbook'?h(WorkbookMetrics,{...props,scope}):h(Metrics,{...props,key:scope,
      configScope:scope,editor:props.editor&&(scope==='draft'||!hasChanges)});
    return h(React.Fragment,null,
      h('div',{className:'nwc-tabs'},h(UI.SegmentedControl,{id:'nwc-metrics',label:'指标口径',value:tab,
        options:[{value:'workbook',label:'内置指标'},{value:'structured',label:'结构化指标'}],onChange:setTab})),
      h('div',{id:'nwc-metrics-'+tab+'-panel',role:'tabpanel','aria-labelledby':'nwc-metrics-'+tab},
        project.draft&&h('div',{className:'nwc-tabs'},h(UI.SegmentedControl,{id:'nwc-metrics-config',label:'指标配置',value:scope,options:views,onChange:setScope})),
        h('div',{id:'nwc-metrics-config-'+scope+'-panel',role:project.draft?'tabpanel':undefined,
          'aria-labelledby':project.draft?'nwc-metrics-config-'+scope:undefined},body)));
  }

  function Metrics({cfg,project,editor,saveDraft,say,configScope}){
    const draft=(configScope==='published'?published(project):draftOf(project))||{terms:[],source_ids:[],tables:{}};
    const [q,setQ]=React.useState(''),[dialog,setDialog]=React.useState(null);
    const reason=r=>r&&((configScope!=='published'&&project.review_reasons?.[r.id])||(r.origin==='excel'&&r.status==='review'&&(r.review_reason||'待复核')));
    const view=r=>r?.config||r?.proposed_config||project.review_candidates?.[r?.id]?.proposed_config||{id:r?.code,source_id:r?.source_id,unit:r?.excel_input?.unit};
    const metrics=draft.terms.filter(r=>isMetric(r)||reason(r)),rows=metrics.filter(r=>!q||(r.name+' '+r.definition+' '+view(r).id).includes(q));
    const sourceName=sid=>cfg.sources.find(s=>s.id===sid)?.name||sid;
    const AGG=[['sum','合计'],['count','计数'],['distinct','不同值计数'],['avg','算术平均'],['min','最小值'],['max','最大值'],['snapshot_sum','期末值合计（库存类）'],['single_value','唯一统计值（重复时待核对）']].map(([id,label])=>({id,label}));
    const blankOperand=n=>({id:n?'value'+n:'value',table:'',column:'',aggregation:'sum',time_column:'',entity_columns:[],filters:[]});
    function formOf(row){const c=view(row);return {name:row?.name||'',definition:row?.definition||'',id:c?.id||'',version:c?.version||'1.0.0',unit:c?.unit||'',source_id:c?.source_id||draft.source_ids[0]||'',time_mode:c?.time_mode||'period',coverage:(c?.coverage||[]).join(', '),formula:c?.formula||'value',operands:c?.operands?.map(o=>({...o,column:o.column||'',time_column:o.time_column||''}))||[{...blankOperand(),table:row?.review_table||''}],dimensions:c?.dimensions||[],relations:c?.relations||[],additive:Boolean(c?.additive)};}
    const sourceOf=v=>cfg.sources.find(s=>s.id===v.source_id);
    const tablesOf=v=>(sourceOf(v)?.tables||[]).filter(t=>(draft.tables[v.source_id]||[]).includes(t.name));
    function check(v){
      if(!v.name.trim()||!v.definition.trim()||!v.unit.trim())return '请填写指标名称、业务定义和单位。';
      if(!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(v.id))return '指标标识需以字母开头，只含字母、数字和下划线。';
      if(!/^\d+\.\d+\.\d+$/.test(v.version))return '版本请使用三个数字，例如 1.0.0。';
      const coverage=v.coverage.split(/[,，\s]+/).filter(Boolean);
      if(v.time_mode==='period'&&(!coverage.length||coverage.some(p=>!/^20\d{2}(-(?:0[1-9]|1[0-2]))?$/.test(p))))return '请填写可查询的期间，例如 2026 或 2026-10，多个用逗号分隔。';
      const tables=tablesOf(v);
      if(v.operands.some(o=>!o.id||!tables.some(t=>t.name===o.table)||(!o.column&&o.aggregation!=='count')||(v.time_mode==='period'&&!o.time_column)))return '请为每个操作数选择授权表、统计字段'+(v.time_mode==='period'?'和时间字段':'')+'。';
      return null;
    }
    function MetricEditor({initial,onClose}){
      const [observed,setObserved]=React.useState(null),[checking,setChecking]=React.useState(false);
      return h(EditDialog,{title:initial?'编辑指标口径':'新增指标口径',wide:true,initial:formOf(initial),saveLabel:editor?(reason(initial)?'确认复核并保存':'保存'):'关闭',onClose,validate:v=>editor&&check(v),
        onSave:v=>{if(!editor)return;const src=sourceOf(v),coverage=v.time_mode==='all'?[]:v.coverage.split(/[,，\s]+/).filter(Boolean);
          const config={...view(initial),kind:'semantic-metric',id:v.id,version:v.version,source_id:v.source_id,source_revision:src?.target_revision||0,unit:v.unit.trim(),definition:v.definition.trim(),time_mode:v.time_mode,coverage,operands:v.operands.map(o=>({...o,column:o.column||null,time_column:v.time_mode==='all'?null:o.time_column})),formula:v.formula,dimensions:v.dimensions,relations:v.relations,additive:v.additive};
          // Keep pack provenance and retrieval metadata as well as hidden metric settings.
          // origin_digest stays unchanged so an administrator's edit is recognized on upgrades.
          const row={...initial,id:initial?.id||uid(),name:v.name.trim(),definition:v.definition.trim(),config,...(reason(initial)?{confirm_review:true}:{})};
          return saveDraft(d=>({...d,terms:initial?d.terms.map(t=>t.id===initial.id?row:t):[...d.terms,row]}),'指标口径已保存并生效。');},
        render:(v,set)=>{const off=!editor,tables=tablesOf(v),colsOf=name=>tables.find(t=>t.name===name)?.columns||[];
          const op=(i,patch)=>set(x=>({operands:x.operands.map((o,j)=>j===i?{...o,...patch}:o)}));
          async function inspect(){setChecking(true);setObserved(null);try{setObserved(await request('inspect_periods',{business_project:project.id,source_id:v.source_id,table:v.operands[0].table,column:v.operands[0].time_column}));}catch(e){setObserved({message:e.message,failed:true});}finally{setChecking(false);}}
          const dimTable=v.operands[0]?.table,dims=colsOf(dimTable);
          return h(React.Fragment,null,
      h(ConfigurationDiagnostics,{project:project,enabled:editor}),
            reason(initial)&&h(Section,{title:'待复核'},h('p',null,reason(initial)),h('p',null,initial.caliber_text)),
            h(Section,{title:'基本信息'},h('div',{className:'nwc-form-grid'},
              h(Text,{label:'指标名称',value:v.name,disabled:off,onChange:name=>set({name})}),
              h(Text,{label:'指标标识',value:v.id,disabled:off,placeholder:'例如 total_amount',onChange:id=>set({id})}),
              h(Text,{label:'单位',value:v.unit,disabled:off,placeholder:'例如 万元、%、个',onChange:unit=>set({unit})}),
              h(Text,{label:'版本',value:v.version,disabled:off,onChange:version=>set({version})})),
              h(Area,{label:'业务定义与统计范围',value:v.definition,disabled:off,rows:3,onChange:definition=>set({definition})})),
            h(Section,{title:'数据与时间'},h('div',{className:'nwc-form-grid'},
              h(Pick,{label:'数据源',value:v.source_id,disabled:off,items:cfg.sources.filter(s=>draft.source_ids.includes(s.id)).map(s=>({id:s.id,label:s.name})),onChange:source_id=>set({source_id,operands:[blankOperand()],dimensions:[],relations:[]})}),
              h(Pick,{label:'统计时间',value:v.time_mode,disabled:off,items:[{id:'period',label:'按期间统计'},{id:'all',label:'全部数据（不分期间）'}],onChange:time_mode=>set({time_mode})})),
              v.time_mode==='period'&&h(Text,{label:'可查询的期间',value:v.coverage,disabled:off,placeholder:'例如 2025, 2026-09, 2026-10',onChange:coverage=>set({coverage})}),
              v.time_mode==='period'&&h('div',{className:'nwc-inline'},h(Button,{size:'sm',variant:'outline',disabled:off||checking||!v.operands[0]?.table||!v.operands[0]?.time_column,onClick:inspect},checking?'正在检查…':'检查时间字段的实际范围'),
                observed&&h('span',{className:observed.failed?'nwc-error':'nwc-sub'},observed.failed?observed.message:'最早 '+(observed.first_observed||'无记录')+'，最晚 '+(observed.latest_observed||'无记录')))),
            h(Section,{title:'计算',extra:editor&&h(Button,{size:'sm',variant:'ghost',disabled:v.operands.length>=10,onClick:()=>set(x=>({operands:[...x.operands,blankOperand(x.operands.length+1)]}))},icon('IconPlusOutlineMedium',14),'添加操作数')},
              v.operands.map((o,i)=>h('div',{key:i,className:'nwc-operand'+(v.time_mode==='period'?'':' nwc-operand-notime')},
                h(Text,{label:'标识',value:o.id,disabled:off,onChange:id=>op(i,{id})}),
                h(Pick,{label:'授权表',value:o.table,disabled:off,items:tables.map(t=>({id:t.name,label:t.label?t.name+' · '+t.label:t.name})),onChange:table=>op(i,{table,column:'',time_column:'',entity_columns:[]})}),
                h(Pick,{label:'聚合方式',value:o.aggregation,disabled:off,items:AGG,onChange:aggregation=>op(i,{aggregation})}),
                h(Pick,{label:'统计字段',value:o.column,disabled:off||!o.table,items:[...(o.aggregation==='count'?[{id:'',label:'全部记录'}]:[]),...colsOf(o.table).map(c=>({id:c.name,label:c.label?c.name+' · '+c.label:c.name}))],onChange:column=>op(i,{column})}),
                v.time_mode==='period'&&h(Pick,{label:'时间字段',value:o.time_column,disabled:off||!o.table,items:colsOf(o.table).map(c=>({id:c.name,label:c.label?c.name+' · '+c.label:c.name})),onChange:time_column=>op(i,{time_column})}),
                h('div',{className:'nwc-operand-remove'},editor&&v.operands.length>1&&h(Button,{size:'sm',variant:'ghost','aria-label':'移除操作数 '+(i+1),onClick:()=>set(x=>({operands:x.operands.filter((_,j)=>j!==i)}))},'移除')))),
              h(Text,{label:'计算公式',value:v.formula,disabled:off,placeholder:'value 或 spent / budget * 100',hint:'引用上面的操作数标识，支持 + − × ÷ 和括号；分母为零时结果为空。',onChange:formula=>set({formula})})),
            reason(initial)&&v.operands.map((o,i)=>{
              const choices=[...(initial.review_filters||project.review_candidates?.[initial.id]?.review_filters||[]),...(view(initial).operands||[]).flatMap(a=>a.filters||[])];
              const unique=[...new Map(choices.map(f=>[JSON.stringify(f),f])).values()];
              if(!unique.length)return null;
              return h(Section,{key:o.id+i,title:(o.id==='numerator'?'分子':o.id==='denominator'?'分母':o.id)+'的筛选条件'},
                unique.map(f=>{const key=JSON.stringify(f),on=(o.filters||[]).some(x=>JSON.stringify(x)===key);
                  return h(Checkbox,{key,label:f.column+' '+({eq:'等于',ne:'不等于',in:'属于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于'}[f.op]||f.op)+' '+f.values.join('、'),checked:on,disabled:off,onChange:yes=>op(i,{filters:yes?[...(o.filters||[]),f]:(o.filters||[]).filter(x=>JSON.stringify(x)!==key)})});}));}),
            h(Section,{title:'分组字段',extra:h('span',{className:'nwc-sub'},dimTable?'已选 '+v.dimensions.length:'先选择第一个操作数的授权表')},
              dims.length>0&&h('div',{className:'nwc-check-grid'},dims.map(c=>h(Checkbox,{key:c.name,label:c.label?c.name+' · '+c.label:c.name,disabled:off,checked:v.dimensions.some(d=>d.table===dimTable&&d.column===c.name),
                onChange:on=>set(x=>({dimensions:on?[...x.dimensions,{id:'dim'+(x.dimensions.length+1)+'_'+c.name.replace(/[^a-zA-Z0-9_]/g,'').slice(0,25),table:dimTable,column:c.name}]:x.dimensions.filter(d=>d.table!==dimTable||d.column!==c.name)}))})))),
            h(Checkbox,{label:'已确认该指标可以按所需范围累加',checked:v.additive,disabled:off,onChange:additive=>set({additive})}));}});
    }
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索指标'}),editor&&h(Button,{size:'sm',variant:'primary',disabled:!draft.source_ids.length,onClick:()=>setDialog({kind:'builder'})},icon('IconPlusOutlineMedium'),'新增指标')),
      h(Card,null,h(Table,{rowKey:r=>r.id,rows,empty:metrics.length?'没有匹配的指标':'还没有结构化指标',columns:[
        {key:'name',title:'指标',render:r=>h('div',null,h('div',{className:'nwc-strong',title:r.name},r.name,reason(r)&&h(Tag,{tone:'warning'},'待复核')),h('div',{className:'nwc-sub',title:sourceName(view(r).source_id)+' · '+view(r).id+' · v'+(view(r).version||'1.0.0')},sourceName(view(r).source_id)+' · '+view(r).id+' · v'+(view(r).version||'1.0.0')))},
        {key:'unit',title:'单位',optional:true,width:'80px',render:r=>view(r).unit||'—'},
        {key:'periods',title:'可查期间',optional:true,width:'140px',render:r=>view(r).time_mode==='all'?'全部数据':(view(r).coverage||[]).length?(view(r).coverage[0]+' 至 '+view(r).coverage[view(r).coverage.length-1]):h(Tag,{tone:'warning'},'未填写')},
        {key:'definition',title:'口径说明',render:r=>h('span',{title:r.definition},r.definition)},
        {key:'ops',title:'操作',width:'210px',wrap:true,render:r=>h(RowActions,{items:[editor&&{label:reason(r)?'复核':'编辑',onClick:()=>setDialog({kind:'builder',row:r})},{label:editor?'高级配置':'查看配置',onClick:()=>setDialog({kind:'json',row:r})},editor&&{label:'删除',danger:true,onClick:()=>setDialog({kind:'remove',row:r})}]})}]})),
      dialog?.kind==='builder'&&h(MetricEditor,{initial:dialog.row||null,onClose:()=>setDialog(null)}),
      dialog?.kind==='json'&&h(EditDialog,{title:'高级配置 · '+dialog.row.name,wide:true,initial:{text:JSON.stringify(view(dialog.row),null,2)},saveLabel:editor?(reason(dialog.row)?'确认复核并保存':'保存'):'关闭',onClose:()=>setDialog(null),
        validate:v=>{if(!editor)return null;try{const c=JSON.parse(v.text);if(!c||typeof c!=='object'||Array.isArray(c))return '需要一个 JSON 对象。';}catch{return 'JSON 格式不正确。';}return null;},
        onSave:v=>{if(!editor)return;const config=JSON.parse(v.text);return saveDraft(d=>({...d,terms:d.terms.map(r=>r.id===dialog.row.id?{...r,config,...(reason(r)?{confirm_review:true}:{})}:r)}),'指标配置已保存到草稿。');},
        render:(v,set)=>h(Area,{label:'结构化指标配置（JSON）',value:v.text,disabled:!editor,rows:16,mono:true,onChange:text=>set({text})})}),
      dialog?.kind==='remove'&&h(Confirm,{title:'删除指标「'+dialog.row.name+'」',description:'删除后对话不能再使用这个指标。',confirmLabel:'删除指标',onClose:()=>setDialog(null),onConfirm:()=>saveDraft(d=>({...d,terms:d.terms.filter(r=>r.id!==dialog.row.id)}),'指标已删除。')}));
  }

  // ----- 查询示例 -----
  function Examples({cfg,project,editor,saveDraft,reload,rev}){
    const draft=draftOf(project),[q,setQ]=React.useState(''),[dialog,setDialog]=React.useState(null),[verification,setVerification]=React.useState(null),[testing,setTesting]=React.useState(false);
    const [review,setReview]=React.useState(null);
    const openReview=async()=>{setTesting(true);try{const value=await request('admin_sessions',{business_project:project.id});const history=await request('admin_validation',{business_project:project.id,action:'review_history'});setReview({sessions:value.sessions,history:history.records,choice:'',category:'delivery',note:''});}catch(error){setReview({error:error.message});}finally{setTesting(false);}};
    const captureReview=async()=>{setTesting(true);try{const selected=review.sessions[Number(review.choice)];if(!selected)throw Error('请选择实际问题。');const value=await request('admin_session_review',{business_project:project.id,...selected,category:review.category,note:review.note});const candidate=value.record.candidates.at(-1);setReview({...review,record:value.record,source_id:candidate?.source_id||draft.source_ids[0],logical_sql:candidate?.sql||'',expectedText:'',assumptions:'',error:''});await reload();}catch(error){setReview({...review,error:error.message});}finally{setTesting(false);}};
    const stageReview=async()=>{setTesting(true);try{const expected=review.expectedText.trim()?JSON.parse(review.expectedText):undefined;await request('admin_validation',{business_project:project.id,action:'stage_review_example',review_id:review.record.id,source_id:review.source_id,logical_sql:review.logical_sql,expected_rows:expected,assumptions:review.assumptions,expected_revision:rev()});await reload();setReview({...review,staged:true,error:''});}catch(error){setReview({...review,error:error.message});}finally{setTesting(false);}};
    const reviewTrial=async(row,r)=>{setTesting(true);try{const value=await request('admin_trial_review',{business_project:project.id,session_id:r.session_id,turn:1,trial_id:r.id});setVerification({row,value});}catch(error){setVerification({row,error:error.message});}finally{setTesting(false);}};
    const trial=async row=>{setTesting(true);try{const value=await request('admin_trial',{business_project:project.id,example_id:row.id});openSession(value.sessionId);}catch(error){setVerification({row,error:error.message});}finally{setTesting(false);}};
    const verify=async(row,action)=>{setTesting(true);try{const value=await request('admin_validation',{business_project:project.id,example_id:row.id,action});setVerification({row,value});}catch(error){setVerification({row,error:error.message});}finally{setTesting(false);}};
    const sources=cfg.sources.filter(s=>draft.source_ids.includes(s.id)),rows=draft.examples.filter(r=>!q||(r.question+' '+r.logical_sql).includes(q));
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索问法或 SQL'}),editor&&h(Button,{size:'sm',variant:'outline',disabled:testing,onClick:openReview},'从会话复核'),editor&&h(Button,{size:'sm',variant:'primary',disabled:!sources.length,onClick:()=>setDialog({row:{id:uid(),question:'',logical_sql:'',source_id:sources[0]?.id||''},isNew:true})},icon('IconPlusOutlineMedium'),'新增示例')),
      h(Card,null,h(Table,{rowKey:r=>r.id,rows,empty:draft.examples.length?'没有匹配的示例':'还没有查询示例',columns:[{key:'question',title:'业务问法',render:r=>h('div',null,h('span',{className:'nwc-strong',title:r.question},r.question),r.origin_review&&h(Tag,{tone:'neutral'},'来自复核'))},{key:'source',title:'数据源',optional:true,width:'160px',render:r=>sources.find(s=>s.id===r.source_id)?.name||'—'},{key:'sql',title:'SQL',render:r=>h('span',{className:'nwc-mono',title:r.logical_sql},r.logical_sql)},
        {key:'ops',title:'操作',width:'180px',wrap:true,render:r=>h(RowActions,{items:[{label:editor?'编辑':'查看',onClick:()=>setDialog({row:r,expectedText:r.expected_rows?JSON.stringify(r.expected_rows,null,2):''})},editor&&r.origin_review&&{label:'原反馈',disabled:testing,onClick:async()=>{setTesting(true);try{const history=await request('admin_validation',{business_project:project.id,action:'review_history'});const orig=history.records.find(x=>x.id===r.origin_review);if(orig){setReview({sessions:[],history:history.records,record:orig,source_id:r.source_id,logical_sql:r.logical_sql,expectedText:r.expected_rows?JSON.stringify(r.expected_rows,null,2):'',assumptions:r.assumptions||'',error:''});}else{setVerification({row:r,error:'未找到关联的原始反馈记录。'});}}catch(e){setVerification({row:r,error:e.message});}finally{setTesting(false);}}},editor&&{label:'原生试问',disabled:testing,onClick:()=>trial(r)},editor&&{label:'试运行',disabled:testing,onClick:()=>verify(r,'run')},editor&&{label:'验证记录',disabled:testing,onClick:()=>verify(r,'history')},editor&&{label:'删除',danger:true,onClick:()=>setDialog({row:r,remove:true})}]})}]})),
      dialog&&!dialog.remove&&h(EditDialog,{title:(dialog.isNew?'新增':'编辑')+'查询示例',initial:{...dialog.row,expectedText:dialog.expectedText||''},wide:true,saveLabel:editor?'保存':'关闭',validate:v=>editor&&(!v.question.trim()||!v.logical_sql.trim())&&'请填写问法和 SQL。',onClose:()=>setDialog(null),
        onSave:v=>{if(!editor)return;const {expectedText,...fields}=v;const row={...fields,question:v.question.trim()};if(expectedText?.trim()){row.expected_rows=JSON.parse(expectedText);if(!Array.isArray(row.expected_rows))throw Error('独立预期须为结果行数组。');}else delete row.expected_rows;return saveDraft(d=>({...d,examples:dialog.isNew?[...d.examples,row]:d.examples.map(r=>r.id===row.id?row:r)}));},
        render:(v,set)=>h(React.Fragment,null,h(Text,{label:'业务问法',value:v.question,disabled:!editor,onChange:question=>set({question})}),h(Pick,{label:'数据源',value:v.source_id,disabled:!editor,items:sources.map(s=>({id:s.id,label:s.name})),onChange:source_id=>set({source_id})}),h(Area,{label:'只读 SQL',value:v.logical_sql,disabled:!editor,rows:10,mono:true,onChange:logical_sql=>set({logical_sql})}),h(Area,{label:'独立预期结果（可选 JSON 行数组）',value:v.expectedText||'',disabled:!editor,rows:5,mono:true,onChange:expectedText=>set({expectedText})}),h('p',{className:'nwc-sub'},'请通过独立 SQL 或已确认业务定义提供预期；模型自身回答不能作为真值。保存成功只表示配置生效。'))}),
      verification&&h(Modal,{open:true,onClose:()=>setVerification(null),title:'示例验证',closeLabel:'关闭'},h('div',{className:'nwc-pad'},
        h('p',null,verification.row.question),verification.error?h('p',{role:'alert'},verification.error):h(React.Fragment,null,
        h('p',null,'配置已生效、执行已验证、答案已核对分别记录。数据或配置变化后需要复测。'),
        ...(verification.value.record?[verification.value.record]:verification.value.records.filter(r=>r.example_id===verification.row.id)).map(r=>h('div',{key:r.id},
          h(Tag,null,r.state),h('p',null,r.freshness==='expired'?'验证已过期':r.freshness==='unknown'?'数据变化状态未知':'对应当前数据与配置'),
          h('p',null,r.invalid_reason|| (r.kind==='native_trial_review'?(r.result_match===true?'查询结果与独立预期一致；回答文字仍需独立复核。':r.result_match===false?'查询结果与独立预期不一致。':'原生试问尚未完成独立核对。'):r.answer_match===true?'已与独立预期逐行核对。':r.answer_match===false?'执行成功，但与独立预期不一致。':'尚未完成独立预期核对。')),
          r.session_id&&h(Button,{size:'sm',variant:'outline',onClick:()=>openSession(r.session_id)},'打开原生会话'),
          r.kind==='native_trial'&&h(Button,{size:'sm',variant:'outline',disabled:testing,onClick:()=>reviewTrial(verification.row,r)},'核对试问结果')))),
        h(Button,{onClick:()=>setVerification(null)},'关闭'))),
      review&&h(Modal,{open:true,onClose:()=>setReview(null),title:'从实际问题形成待复核示例',closeLabel:'关闭'},h('div',{className:'nwc-pad'},
        review.error&&h('p',{role:'alert'},review.error),
        review.staged?h('p',null,'已保存为待复核草稿。请检查未生效的改动，确认后应用，再运行示例和原生试问；原问题与失败记录继续保留。'):
        review.record?h(React.Fragment,null,h('p',null,review.record.question),h('p',{className:'nwc-sub'},'原回答（不作为真值）：'+review.record.answer),
          h(Pick,{label:'数据源',value:review.source_id,items:sources.map(s=>({id:s.id,label:s.name})),onChange:source_id=>setReview({...review,source_id})}),
          h(Area,{label:'待复核的只读 SQL',value:review.logical_sql,rows:7,mono:true,onChange:logical_sql=>setReview({...review,logical_sql})}),
          h(Area,{label:'适用前提',value:review.assumptions,onChange:assumptions=>setReview({...review,assumptions})}),
          h(Area,{label:'独立预期结果（JSON 行数组）',value:review.expectedText,rows:4,mono:true,onChange:expectedText=>setReview({...review,expectedText})}),
          h(Button,{disabled:testing||!review.logical_sql.trim(),onClick:stageReview},'保存待复核草稿')):
        review.sessions&&h(React.Fragment,null,
          review.history?.filter(r=>r.kind==='session_feedback').length>0&&h(Section,{title:'已记录的问题'},review.history.filter(r=>r.kind==='session_feedback').map(r=>h('div',{key:r.id},h('p',null,r.question),h('p',{className:'nwc-sub'},r.note),h(Button,{size:'sm',variant:'outline',onClick:()=>openSession(r.session_id)},'打开原会话'),h(Button,{size:'sm',variant:'outline',onClick:()=>{const c=r.candidates.at(-1);setReview({...review,record:r,source_id:c?.source_id||draft.source_ids[0],logical_sql:c?.sql||'',expectedText:'',assumptions:'',error:''});}},'继续复核'),draft.examples.filter(e=>e.origin_review===r.id).map(e=>h(Button,{key:e.id,size:'sm',variant:'outline',onClick:()=>{setReview(null);verify(e,'history');}},'关联示例验证记录'))))),
          h(Pick,{label:'本项目的实际问题',value:review.choice,items:review.sessions.map((s,i)=>({id:String(i),label:s.question})),onChange:choice=>setReview({...review,choice})}),
          !review.sessions.length&&h('p',null,'暂无可复核的已绑定会话。'),
          h(Pick,{label:'问题分类',value:review.category,items:[['scope','范围'],['relation','关联'],['retrieval','检索'],['term','术语'],['delivery','交付']].map(([id,label])=>({id,label})),onChange:category=>setReview({...review,category})}),
          h(Area,{label:'需要纠正的行为',value:review.note,onChange:note=>setReview({...review,note})}),h('p',{className:'nwc-sub'},'反馈会保留为待复核记录，不自动修改已生效口径，也不把原回答当成标准答案。'),
          h(Button,{disabled:testing||review.choice===''||!review.note.trim(),onClick:captureReview},'记录并准备示例')),
        h(Button,{variant:'outline',onClick:()=>setReview(null)},'关闭'))),
      dialog?.remove&&h(Confirm,{title:'删除查询示例',description:clip(dialog.row.question,80),confirmLabel:'删除示例',onClose:()=>setDialog(null),onConfirm:()=>saveDraft(d=>({...d,examples:d.examples.filter(r=>r.id!==dialog.row.id)}),'示例已删除。')}));
  }

  // ----- 分析技能 -----
  function Skills({project,editor,admin,saveDraft,say}){
    const draft=draftOf(project),[tab,setTab]=React.useState('installed');
    return h(React.Fragment,null,
      h('div',{className:'nwc-tabs'},h(UI.SegmentedControl,{value:tab,label:'分析技能',options:[{value:'installed',label:'已安装技能'},{value:'notes',label:'分析方法说明（'+draft.skills.length+'）'}],onChange:setTab})),
      tab==='installed'?h(InstalledSkills,{project,editable:admin,say}):h(SkillNotes,{project,editor,saveDraft}));
  }
  function InstalledSkills({project,editable,say}){
    const [data,setData]=React.useState(null),[q,setQ]=React.useState(''),[busy,setBusy]=React.useState(false);
    const load=()=>getJson('project_skills',{business_project:project.id}).then(setData);
    React.useEffect(()=>{load().catch(e=>say(e.message,true));},[project.id]);
    if(!data)return h(Empty,null,'正在读取技能…');
    async function toggle(skill,value){setBusy(true);try{await request('policy_update',{project:project.id,business_project:project.id,scope:'project',changes:{},skills:{[skill.id]:value},expected_revision:data.revision});await load();}catch(e){say(e.message,true);await load().catch(()=>{});}finally{setBusy(false);}}
    const rows=data.skills.filter(s=>!q||(s.title+' '+(s.category||'')).toLowerCase().includes(q.toLowerCase()));
    const on=data.skills.filter(s=>s.enabled&&s.applicable).length;
    return h(React.Fragment,null,
      h(Toolbar,null,h(Search,{value:q,onChange:setQ,placeholder:'搜索技能'}),h('span',{className:'nwc-sub'},'已启用 '+on+' / '+data.skills.length)),
      h(Card,null,h(Table,{rowKey:s=>s.id,rows,columns:[{key:'title',title:'技能',render:s=>h('span',{className:'nwc-strong',title:s.title},s.title)},{key:'category',title:'分类',optional:true,width:'140px',render:s=>s.category||'扩展技能'},{key:'requires',title:'适用条件',render:s=>{const t=String(s.requires||'').replace(/\s*（?(?:options\.\w+|comparison_result_ref)）?/g,'').replace(/\s*dimensions\s*/g,'').replace(/\s*columns\s*/g,'')+(s.applicable?'':'（当前项目缺少所需指标）');return h('span',{title:t},t);}},
        {key:'on',title:'启用',width:'80px',wrap:true,render:s=>h(Switch,{label:'启用'+s.title,checked:s.enabled&&s.applicable,disabled:busy||!editable||!s.applicable,onChange:v=>toggle(s,v)})}]})));
  }
  function SkillNotes({project,editor,saveDraft}){
    const draft=draftOf(project),[dialog,setDialog]=React.useState(null);
    return h(React.Fragment,null,
      h(Toolbar,null,h('span',{className:'nwc-sub'},'说明业务上的分析方法和适用范围，供问答参考。'),editor&&h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog({row:{id:uid(),name:'',description:''},isNew:true})},icon('IconPlusOutlineMedium'),'新增说明')),
      h(Card,null,h(Table,{rowKey:r=>r.id,rows:draft.skills,empty:'还没有分析方法说明',columns:[{key:'name',title:'名称',width:'200px',render:r=>h('span',{className:'nwc-strong'},r.name)},{key:'description',title:'适用范围与业务规则',render:r=>h('span',{title:r.description},clip(r.description,90))},
        {key:'ops',title:'操作',width:'150px',wrap:true,render:r=>h(RowActions,{items:[{label:editor?'编辑':'查看',onClick:()=>setDialog({row:r})},editor&&{label:'删除',danger:true,onClick:()=>setDialog({row:r,remove:true})}]})}]})),
      dialog&&!dialog.remove&&h(EditDialog,{title:(dialog.isNew?'新增':'编辑')+'分析方法说明',initial:dialog.row,wide:true,saveLabel:editor?'保存':'关闭',validate:v=>editor&&!v.name.trim()&&'请填写名称。',onClose:()=>setDialog(null),
        onSave:v=>{if(!editor)return;const row={...v,name:v.name.trim()};return saveDraft(d=>({...d,skills:dialog.isNew?[...d.skills,row]:d.skills.map(r=>r.id===row.id?row:r)}));},
        render:(v,set)=>h(React.Fragment,null,h(Text,{label:'名称',value:v.name,disabled:!editor,onChange:name=>set({name})}),h(Area,{label:'适用范围与业务规则',value:v.description,disabled:!editor,rows:6,onChange:description=>set({description})}))}),
      dialog?.remove&&h(Confirm,{title:'删除「'+dialog.row.name+'」',confirmLabel:'删除',onClose:()=>setDialog(null),onConfirm:()=>saveDraft(d=>({...d,skills:d.skills.filter(r=>r.id!==dialog.row.id)}),'已删除。')}));
  }

  // ----- 口径导入 -----
  // Same interfaces as the settings-page PackImport / TemplateTransfer (business_packs,
  // import_pack, business_template, import_template); laid out as a table plus dialogs.
  function Imports({cfg,project,act,admin,editor,say}){
    const [packs,setPacks]=React.useState(null),[error,setError]=React.useState(''),[dialog,setDialog]=React.useState(null),[exporting,setExporting]=React.useState(false);
    const scopeKey=(project.draft?.source_ids||[]).join(',')+':'+JSON.stringify(project.draft?.tables||{}).length;
    React.useEffect(()=>{let live=true;request('business_packs',{business_project:project.id}).then(v=>{if(live){setPacks(v.packs||[]);setError('');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[project.id,scopeKey]);
    const packMessage=v=>{const r=v?.pack_report||{},i=r.imported||{},sk=r.skipped||[];return '已导入：字段与取值 '+(i.mappings||0)+' 条、口径说明 '+(i.terms||0)+' 条、结构化指标 '+(i.metrics||0)+' 个、查询示例 '+(i.examples||0)+' 个'+(sk.length?'；跳过 '+sk.length+' 项':'')+'。尚未生效，请点顶部「未生效的改动」核对后生效。';};
    async function exportTemplate(){setExporting(true);try{const value=await request('business_template',{business_project:project.id});const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='ask-data-business-template.json';a.click();URL.revokeObjectURL(url);}catch(e){say(e.message,true);}finally{setExporting(false);}}
    const sourceName=id=>cfg.sources.find(s=>s.id===id)?.name||id;
    return h(React.Fragment,null,
      h(Card,{title:'内置口径包'},
        error?h('p',{role:'alert',className:'nwc-error nwc-pad'},error):!packs?h(Empty,null,'正在读取…'):
        h(Table,{rowKey:p=>p.id,rows:packs,empty:'没有内置口径包',columns:[
          {key:'name',title:'口径包',render:p=>h('div',null,h(NameLine,{text:p.name+' · v'+p.version},p.imported_rows?h(Tag,{tone:'success'},'已导入'):null),h('div',{className:'nwc-sub',title:p.description},p.description))},
          {key:'content',title:'内容',optional:true,width:'170px',render:p=>{const t=p.tables+' 张表 · '+p.terms+' 口径 · '+p.examples+' 查询示例'+(p.metrics?' · '+p.metrics+' 结构化指标':'');return h('span',{title:t},t);}},
          {key:'match',title:'匹配当前项目',width:'130px',render:p=>{const m=p.matches[0];return !m?h(Tag,{tone:'quiet'},'没有匹配的表'):h('span',{title:p.matches.map(x=>sourceName(x.source_id)+'：匹配 '+x.matched_tables+' 张'+(x.missing_tables.length?'，缺少 '+x.missing_tables.length+' 张':'')).join('\n')},m.matched_tables+' 张'+(m.missing_tables.length?'（缺 '+m.missing_tables.length+'）':''));}},
          {key:'ops',title:'操作',width:'150px',wrap:true,render:p=>h(RowActions,{items:[editor&&{label:p.imported_rows?'重新导入':'导入',disabled:!p.matches.length,onClick:()=>setDialog({kind:'pack',pack:p})},admin&&{label:'全量替换',disabled:!p.matches.some(m=>!m.missing_tables.length),onClick:()=>setDialog({kind:'pack',pack:p,replace:true})},p.pending?.length>0&&{label:'待确认 '+p.pending.length,onClick:()=>setDialog({kind:'pending',pack:p})}]})}]})),
      h(Card,{title:'业务模板',actions:h(React.Fragment,null,h(Button,{size:'sm',variant:'outline',disabled:exporting,onClick:exportTemplate},exporting?'正在导出…':'导出模板'),editor&&h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog({kind:'template'})},'导入模板'))},
        h('p',{className:'nwc-sub nwc-pad'},'模板包含字段映射、口径说明、指标和查询示例，不含连接凭据、用户授权和历史数据。导出后请自行检查其中的业务敏感信息。')),
      dialog?.kind==='pack'&&h(PackDialog,{pack:dialog.pack,sourceName,replace:dialog.replace,onClose:()=>setDialog(null),onImport:sid=>act('import_pack',{pack:dialog.pack.id,source_id:sid,...(dialog.replace?{replace_calibers:true}:{})},packMessage)}),
      dialog?.kind==='pending'&&h(Dialog,{open:true,title:'待业务确认的口径 · '+dialog.pack.name,onClose:()=>setDialog(null),footer:h(Button,{size:'sm',variant:'primary',onClick:()=>setDialog(null)},'关闭')},h('ol',{className:'nwc-list'},dialog.pack.pending.map((x,k)=>h('li',{key:k},x)))),
      dialog?.kind==='template'&&h(TemplateDialog,{project,sources:cfg.sources.filter(x=>(project.draft?.source_ids||[]).includes(x.id)),onClose:()=>setDialog(null),onImport:(template,bindings)=>act('import_template',{template,source_bindings:bindings},'模板已导入，尚未生效。请点顶部「未生效的改动」核对后生效。')}));
  }
  function PackDialog({pack,sourceName,replace,onClose,onImport}){
    const matches=replace?pack.matches.filter(m=>!m.missing_tables.length):pack.matches;
    const [sid,setSid]=React.useState(matches[0]?.source_id||''),m=matches.find(x=>x.source_id===sid);
    return h(Confirm,{title:(replace?'全量替换为':pack.imported_rows?'重新导入':'导入')+'「'+pack.name+'」',danger:replace,confirmLabel:replace?'生成替换草稿':'导入',blocked:!sid,onClose,onConfirm:()=>onImport(sid),
      description:replace?'以选定的内置口径包重建项目口径草稿。生效后，问数统一使用新口径。数据库授权与会话历史独立管理。':'导入字段中文名、取值、口径说明、结构化指标和查询示例。库里缺少的表或字段会跳过；已有字段映射全部保留，只补充缺项，人工修改的口径和示例也保留。导入后需在顶部「未生效的改动」中核对并生效。'},
      matches.length>1&&h(Pick,{label:'数据源',value:sid,items:matches.map(x=>({id:x.source_id,label:sourceName(x.source_id)+' · 匹配 '+x.matched_tables+' 张表'})),onChange:setSid}),
      m&&h('p',{className:'nwc-sub'},'匹配 '+m.matched_tables+' 张表'+(m.missing_tables.length?'，缺少：'+m.missing_tables.join('、'):'。')));
  }
  function TemplateDialog({sources,onClose,onImport}){
    const input=React.useRef(null),[file,setFile]=React.useState(null),[template,setTemplate]=React.useState(null),[bindings,setBindings]=React.useState({}),[error,setError]=React.useState(''),[busy,setBusy]=React.useState(false);
    async function choose(e){const f=e.target.files?.[0];e.target.value='';if(!f)return;setError('');setTemplate(null);setBindings({});if(f.size>300000){setError('模板文件超过 300 KB。');return;}setFile(f.name);try{setTemplate(JSON.parse(await f.text()));}catch{setError('不是有效的模板文件（JSON 格式错误）。');}}
    const ready=template&&(template.sources||[]).every(s=>bindings[s.alias]);
    async function go(){setBusy(true);setError('');try{await onImport(template,bindings);onClose();}catch(e){setError(e.message);}finally{setBusy(false);}}
    return h(Dialog,{open:true,title:'导入业务模板',onClose,footer:h('div',{className:'nwc-actions'},h(Button,{size:'sm',variant:'ghost',disabled:busy,onClick:onClose},'取消'),h(Button,{size:'sm',variant:'primary',disabled:busy||!ready,onClick:go},busy?'正在导入…':'校验并导入'))},
      // Native file input is hidden; the DSH button opens it.
      h('input',{ref:input,type:'file',accept:'.json,application/json',hidden:true,onChange:choose}),
      h(Field,{label:'模板文件'},h('div',{className:'nwc-inline'},h(Button,{size:'sm',variant:'outline',onClick:()=>input.current?.click()},file?'重新选择':'选择文件'),h('span',{className:file?'':'nwc-sub'},file||'未选择（.json，300 KB 以内）'))),
      template&&(template.sources||[]).map(s=>h(Pick,{key:s.alias,label:'模板中的「'+s.alias+'」对应',value:bindings[s.alias]||'',items:sources.map(x=>({id:x.id,label:x.name})),onChange:id=>setBindings(b=>({...b,[s.alias]:id}))})),
      template&&!(template.sources||[]).length&&h('p',{className:'nwc-sub'},'模板没有声明数据源。'),
      error&&h('p',{role:'alert',className:'nwc-error'},error));
  }

  // ----- Excel 批量导入（接口：excel_template / excel_import / excel_history） -----
  const EXCEL_RESULT={added:['新增','success'],updated:['更新','info'],unchanged:['无变化','quiet'],review:['待复核','warning'],rejected:['被拒','error']};
  function downloadBase64(name,b64,type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'){
    const bin=atob(b64),bytes=new Uint8Array(bin.length);for(let k=0;k<bin.length;k++)bytes[k]=bin.charCodeAt(k);
    const url=URL.createObjectURL(new Blob([bytes],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const readBase64=file=>new Promise((ok,fail)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(',')[1]||'');r.onerror=()=>fail(Error('读取文件失败。'));r.readAsDataURL(file);});
  function ExcelImport({project,reload,say,rev}){
    const input=React.useRef(null),[busy,setBusy]=React.useState(''),[preview,setPreview]=React.useState(null),[filter,setFilter]=React.useState('all'),[history,setHistory]=React.useState(null),[error,setError]=React.useState('');
    const loadHistory=()=>request('excel_history',{business_project:project.id}).then(v=>setHistory(v.items||[])).catch(()=>setHistory([]));
    React.useEffect(()=>{loadHistory();},[]);
    async function run(kind,fn){setBusy(kind);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
    const template=prefill=>run(prefill?'prefill':'blank',async()=>{const v=await request('excel_template',{business_project:project.id,prefill});downloadBase64(v.name,v.content_base64);});
    async function choose(e){const f=e.target.files?.[0];e.target.value='';if(!f)return;
      if(!/\.xlsx$/i.test(f.name)){setError('请选择 .xlsx 格式的文件。');return;}
      if(f.size>5*1048576){setError('文件超过 5 MB。');return;}
      await run('preview',async()=>{setPreview(null);const v=await request('excel_import',{business_project:project.id,mode:'preview',name:f.name,content_base64:await readBase64(f)});setPreview({...v,file:f.name});setFilter(v.summary?.rejected?'rejected':'all');});}
    const apply=()=>run('apply',async()=>{const v=await request('excel_import',{business_project:project.id,mode:'apply',token:preview.token,expected_revision:rev()});await reload();setPreview(null);loadHistory();
      const c=v.import_counts||{};say('已写入：新增 '+(c.added||0)+' 条，更新 '+(c.updated||0)+' 条。尚未生效，请点顶部「未生效的改动」核对后生效。');});
    const sum=preview?.summary||{},writable=(sum.added||0)+(sum.updated||0)+(sum.review||0);
    const rows=(preview?.rows||[]).filter(r=>filter==='all'||r.result===filter);
    const counts=x=>Object.entries(EXCEL_RESULT).filter(([k])=>x[k]).map(([k,[l]])=>l+' '+x[k]).concat(x.missing?['表中未列出 '+x.missing]:[]).join(' · ')||'没有变化';
    return h(React.Fragment,null,
      h(Card,{title:'模板',actions:h(React.Fragment,null,h(Button,{size:'sm',variant:'outline',disabled:Boolean(busy),onClick:()=>template(false)},busy==='blank'?'正在生成…':'下载空白模板'),h(Button,{size:'sm',variant:'outline',disabled:Boolean(busy),onClick:()=>template(true)},busy==='prefill'?'正在生成…':'下载含已有口径的模板'))},
        h('p',{className:'nwc-sub nwc-pad'},'在模板里填写或修改业务口径，再上传。上传后先预览逐行检查结果，确认后才写入。')),
      h(Card,{title:'上传与预览',actions:h(React.Fragment,null,
          h('input',{ref:input,type:'file',accept:'.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',hidden:true,onChange:choose}),
          preview?.receipt_base64&&h(Button,{size:'sm',variant:'outline',onClick:()=>downloadBase64('校验回执_'+preview.file,preview.receipt_base64)},'下载校验回执'),
          h(Button,{size:'sm',variant:preview?'outline':'primary',disabled:Boolean(busy),onClick:()=>input.current?.click()},busy==='preview'?'正在检查…':preview?'重新选择文件':'选择 .xlsx 文件'),
          preview&&h(Button,{size:'sm',variant:'primary',disabled:Boolean(busy)||!writable,onClick:apply},busy==='apply'?'正在写入…':'写入'))},
        error&&h('p',{role:'alert',className:'nwc-error nwc-pad'},error),
        !preview?h('p',{className:'nwc-sub nwc-pad'},'还没有上传文件（.xlsx，5 MB 以内）。'):h(React.Fragment,null,
          h('div',{className:'nwc-pad nwc-inline'},h('span',{className:'nwc-strong'},preview.file),h('span',{className:'nwc-sub'},counts(sum)),
            h(FilterMenu,{label:'结果',value:filter,onChange:setFilter,items:[['all','全部'],...Object.entries(EXCEL_RESULT).map(([k,[l]])=>[k,l])].map(([id,l])=>({id,label:l+'（'+(id==='all'?(preview.rows||[]).length:(sum[id]||0))+'）'}))})),
          (preview.unknown_columns||[]).length>0&&h('div',{className:'nwc-pad'},h('div',{className:'nwc-notice is-warning'},'这些列不认识，已忽略：'+preview.unknown_columns.join('、'))),
          sum.rejected>0&&h('p',{className:'nwc-sub nwc-pad'},'被拒的行不会写入；可下载校验回执，改好后重新上传。'),
          h(Table,{rowKey:r=>r.row+':'+r.code,rows,empty:'没有符合筛选的行',columns:[
            {key:'row',title:'行',width:'50px',render:r=>String(r.row)},
            {key:'name',title:'口径',render:r=>h('div',null,h('div',{className:'nwc-strong',title:r.name},r.name||'—'),h('div',{className:'nwc-sub',title:r.code},(r.code||'')+(r.version?' · v'+r.version:'')))},
            {key:'result',title:'结果',width:'80px',render:r=>{const [l,t]=EXCEL_RESULT[r.result]||[r.result,'neutral'];return h(Tag,{tone:t},l);}},
            {key:'msg',title:'原因',render:r=>{const t=(r.messages||[]).map(m=>m.text).join('；');return h('span',{className:(r.messages||[]).some(m=>m.level==='reject')?'nwc-error-text':undefined,title:t},t||'—');}}]}))),
      h(Card,{title:'导入记录'},history===null?h(Empty,null,'正在读取…'):h(Table,{rowKey:(x,k)=>k,rows:history,empty:'还没有导入记录',columns:[
        {key:'at',title:'时间',width:'110px',render:x=>shortTime(x.at)},
        {key:'name',title:'文件',render:x=>h('span',{title:x.name},x.name)},
        {key:'actor',title:'操作人',optional:true,width:'130px',render:x=>h('span',{title:x.actor},x.actor)},
        {key:'counts',title:'结果',render:x=>{const t=counts(x);return h('span',{title:t},t);}}]})));
  }

  const TARGET_TYPE_NAMES={metric:'指标',example:'示例 / 代表问题',verification_record:'验证记录'};

  // ----- 发布中心 -----
  // Changes saved but not live (a bulk import, or a save whose publish check failed).
  function Changes({cfg,project,editor,act,pending,say,onClose}){
    const [check,setCheck]=React.useState(null),[busy,setBusy]=React.useState(''),[error,setError]=React.useState('');
    const [impact,setImpact]=React.useState(null),[loadingImpact,setLoadingImpact]=React.useState(false);
    const tone={新增:'success',修改:'info',删除:'error',移除:'error'};
    const loadImpact=async()=>{
      setLoadingImpact(true);
      try{
        const r=await request('admin_validation',{business_project:project.id,action:'impact',draft:draftOf(project)});
        setImpact(r.impact);
      }catch(_){}
      finally{setLoadingImpact(false);}
    };
    React.useEffect(()=>{loadImpact();},[project.id]);
    async function run(kind,fn){setBusy(kind);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
    const validate=()=>run('check',async()=>{
      setCheck(await request('admin_check',{business_project:project.id,expected_revision:cfg.revision,draft:draftOf(project)}));
      await loadImpact();
    });
    const apply=()=>run('apply',async()=>{await act('publish',{},'改动已生效。');onClose();});
    const discard=()=>run('discard',async()=>{await act('draft',{draft:structuredClone(published(project))},'已放弃未生效的改动。');onClose();});
    return h(Dialog,{open:true,wide:true,title:'未生效的改动与影响预览',onClose,footer:h('div',{className:'nwc-dialog-footer'},
        h('div',{className:'nwc-actions'},editor&&project.published_version&&h(Button,{size:'sm',variant:'ghost',className:'nwc-danger-text',disabled:Boolean(busy),onClick:discard},busy==='discard'?'正在放弃…':'放弃这些改动')),
        h('div',{className:'nwc-actions'},editor&&h(Button,{size:'sm',variant:'outline',disabled:Boolean(busy),onClick:validate},busy==='check'?'正在检查…':'检查与依赖分析'),editor&&h(Button,{size:'sm',variant:'primary',disabled:Boolean(busy),onClick:apply},busy==='apply'?'正在生效…':'全部生效')))},
      h('p',{className:'nwc-sub'},project.published_version?'下面的改动已保存到草稿，尚未应用。纯只读影响预览，不写发布配置；问数仍使用 '+time(liveEntry(project)?.at)+' 生效的配置。':'下面的改动尚未应用，此项目还不能用于问数。'),
      check&&h('div',{role:check.valid?'status':'alert',className:'nwc-notice'+(check.valid?'':' is-error')},check.valid?(check.message||'检查通过。'):h('ul',null,check.errors.map((e,k)=>h('li',{key:k},e.message)))),
      h(Section,{title:'未生效改动项（'+pending.length+'）'},
        h(Table,{rowKey:(r,k)=>k,rows:pending,empty:'没有未生效的改动',columns:[{key:'area',title:'分类',width:'120px'},{key:'kind',title:'变更',width:'70px',render:r=>h(Tag,{tone:tone[r.kind]||'neutral'},r.kind)},{key:'name',title:'条目',render:r=>h('span',{title:r.name},r.name)}]})),
      loadingImpact?h('p',{className:'nwc-sub nwc-pad'},'正在基于 SQL AST 与语义合同分析依赖与影响…'):
      impact&&h(React.Fragment,null,
        h(Section,{title:'已知受影响项（'+(impact.known_impacts?.length||0)+'）',extra:impact.known_impacts?.length>0&&h(Tag,{tone:'error'},'需重新复测/已失效')},
          h(Table,{rowKey:(r,k)=>k,rows:impact.known_impacts||[],empty:'未检测到因本次变更直接失效的指标、示例或验证记录',columns:[
            {key:'target_type',title:'对象类型',width:'110px',render:r=>TARGET_TYPE_NAMES[r.target_type]||r.target_type},
            {key:'name',title:'条目 / 代表问题',width:'200px',render:r=>h('span',{className:'nwc-strong',title:r.name},r.name)},
            {key:'impact_label',title:'影响判定',width:'110px',render:r=>h(Tag,{tone:'error'},r.impact_label)},
            {key:'reason',title:'原因',render:r=>h('span',{title:r.reason},r.reason)}
          ]})),
        (impact.pending_dependencies?.length>0)&&h(Section,{title:'待核对依赖（'+impact.pending_dependencies.length+'）',extra:h(Tag,{tone:'warning'},'待核对')},
          h(Table,{rowKey:(r,k)=>k,rows:impact.pending_dependencies,empty:'无待核对依赖',columns:[
            {key:'target_type',title:'对象类型',width:'110px',render:r=>TARGET_TYPE_NAMES[r.target_type]||r.target_type},
            {key:'name',title:'条目 / 代表问题',width:'200px',render:r=>h('span',{className:'nwc-strong',title:r.name},r.name)},
            {key:'impact_label',title:'关注点',width:'110px',render:r=>h(Tag,{tone:'warning'},r.impact_label)},
            {key:'reason',title:'原因',render:r=>h('span',{title:r.reason},r.reason)}
          ]})),
        h('p',{className:'nwc-sub nwc-pad'},'影响范围说明：共有 '+(impact.summary?.unaffected_count||0)+' 项无直接依赖的指标与验证记录继续保持有效。')),
      error&&h('p',{role:'alert',className:'nwc-error'},error));
  }
  function Versions({project,editor,admin,act}){
    const [dialog,setDialog]=React.useState(null);
    const {historyImpact,loadingImpact}=dialog||{};
    const rows=[...project.versions].reverse();
    const scopeChanged=v=>['source_ids','tables'].some(f=>JSON.stringify(v.config[f])!==JSON.stringify(draftOf(project)[f]));
    const historyChanges=v=>{if(Array.isArray(v.changes))return v.changes;const previous=project.versions.filter(x=>x.version<v.version).sort((a,b)=>b.version-a.version)[0];return previous?changes({draft:v.config,published_version:previous.version,versions:[previous]}):null;};
    const summary=v=>{const list=historyChanges(v);if(!list)return v.note||'历史配置';if(!list.length)return v.note||'配置未变化';return list.slice(0,2).map(x=>x.kind+'「'+x.name+'」').join('；')+(list.length>2?' 等 '+list.length+' 项':'');};

    const openDetails=row=>{
      const detail={kind:'details',row,loadingImpact:editor};
      setDialog(detail);
      if(!editor)return;
      const finish=historyImpact=>setDialog(current=>current===detail?{...detail,loadingImpact:false,historyImpact}:current);
      request('admin_validation',{business_project:project.id,action:'impact',version:row.version})
        .then(res=>finish(res.impact),()=>finish(null));
    };

    return h(React.Fragment,null,
      h(Toolbar,null,h('span',{className:'nwc-sub'},'保留最近 3 个恢复点。更早记录完整归档，相同内容不重复记录，恢复操作也会留下记录。')),
      h(Card,null,h(Table,{rowKey:v=>v.version,rows,empty:'还没有生效记录',columns:[
        {key:'at',title:'生效时间',width:'180px',render:v=>h('span',null,shortTime(v.at),v.version===project.published_version&&h(Tag,{tone:'success'},'当前'))},
        {key:'summary',title:'变更内容',render:v=>{const label=summary(v);return h('div',null,h('span',{title:label},label),v.restored_from_at&&h('div',{className:'nwc-sub'},'恢复自 '+time(v.restored_from_at)));}},
        {key:'actor',title:'修改人',width:'140px',render:v=>v.actor||'—'},
        {key:'ops',title:'操作',width:'180px',wrap:true,render:v=>h(RowActions,{items:[
          {label:'查看改动',onClick:()=>openDetails(v)},
          editor&&v.version!==project.published_version&&{label:'恢复此配置',disabled:!admin&&scopeChanged(v),onClick:()=>setDialog({kind:'restore',row:v})}]})}]})),
      dialog?.kind==='details'&&h(Dialog,{open:true,wide:true,title:time(dialog.row.at)+' 的配置变更与影响详情',onClose:()=>setDialog(null)},
        h('p',{className:'nwc-sub'},'修改人：'+(dialog.row.actor||'—')+'。'+(dialog.row.note||'')),
        h(Section,{title:'变更条目'},
          historyChanges(dialog.row)?h(Table,{rowKey:(r,k)=>k,rows:historyChanges(dialog.row),empty:'本次没有内容变化',columns:[{key:'area',title:'分类',width:'130px'},{key:'kind',title:'变更',width:'70px'},{key:'name',title:'条目'}]}):h(Empty,null,'这条旧记录没有逐项变更摘要，完整配置仍可恢复。')),
        loadingImpact?h('p',{className:'nwc-sub nwc-pad'},'正在基于依赖图分析该次变更的具体影响…'):
        historyImpact&&h(React.Fragment,null,
          h(Section,{title:'受本次变更影响项（'+(historyImpact.known_impacts?.length||0)+'）',extra:historyImpact.known_impacts?.length>0&&h(Tag,{tone:'error'},'已失效/口径变化')},
            h(Table,{rowKey:(r,k)=>k,rows:historyImpact.known_impacts||[],empty:'本次变更未直接导致既有指标或验证记录失效',columns:[
              {key:'target_type',title:'对象类型',width:'110px',render:r=>TARGET_TYPE_NAMES[r.target_type]||r.target_type},
              {key:'name',title:'条目 / 代表问题',width:'200px',render:r=>h('span',{className:'nwc-strong',title:r.name},r.name)},
              {key:'impact_label',title:'影响判定',width:'110px',render:r=>h(Tag,{tone:'error'},r.impact_label)},
              {key:'reason',title:'原因',render:r=>h('span',{title:r.reason},r.reason)}
            ]})),
          (historyImpact.pending_dependencies?.length>0)&&h(Section,{title:'待核对依赖（'+historyImpact.pending_dependencies.length+'）',extra:h(Tag,{tone:'warning'},'待核对')},
            h(Table,{rowKey:(r,k)=>k,rows:historyImpact.pending_dependencies,empty:'无待核对依赖',columns:[
              {key:'target_type',title:'对象类型',width:'110px',render:r=>TARGET_TYPE_NAMES[r.target_type]||r.target_type},
              {key:'name',title:'条目 / 代表问题',width:'200px',render:r=>h('span',{className:'nwc-strong',title:r.name},r.name)},
              {key:'impact_label',title:'关注点',width:'110px',render:r=>h(Tag,{tone:'warning'},r.impact_label)},
              {key:'reason',title:'原因',render:r=>h('span',{title:r.reason},r.reason)}
            ]})),
          h('p',{className:'nwc-sub nwc-pad'},'影响范围说明：当时共有 '+(historyImpact.summary?.unaffected_count||0)+' 项无直接依赖的指标与验证记录继续保持有效。'))),
      dialog?.kind==='restore'&&h(Confirm,{title:'恢复历史配置',description:'将恢复到 '+time(dialog.row.at)+' 的配置并立即生效。当前配置和未应用的改动会被替换；本次恢复会记录到变更历史。',confirmLabel:'恢复并生效',onClose:()=>setDialog(null),onConfirm:()=>act('rollback',{version:dialog.row.version},'历史配置已恢复并生效。')}));
  }

  return {Console,changes,workbookMetricRows};
}

export const consoleCss=`
.nwc{container-type:inline-size;display:grid;grid-template-columns:auto minmax(0,1fr);height:100%;min-height:0;color:var(--dsw-alias-label-primary);background:inherit;font-size:13px}
.nwc-loading{grid-column:1/-1;padding:32px;display:flex;gap:12px;align-items:center}
.nwc-nav{width:216px;box-sizing:border-box;border-right:1px solid var(--dsw-alias-border-l2);overflow:auto;padding:16px 10px;display:flex;flex-direction:column;gap:14px}
.nwc-brand{display:flex;align-items:center;justify-content:space-between;padding:0 6px 6px;font-size:15px;font-weight:500}.nwc-brand-version{font-size:12px;font-weight:400;color:var(--dsw-alias-label-secondary);white-space:nowrap}
.nwc-nav-title{font-size:12px;color:var(--dsw-alias-label-secondary);padding:0 8px 4px}
.nwc-nav-item{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:8px;width:100%;padding:7px 8px;border-radius:8px;cursor:pointer;color:var(--dsw-alias-label-primary)}
.nwc-nav-item:hover{background:var(--dsw-alias-fill-l1,rgba(127,127,127,.08))}
.nwc-nav-item.is-active{background:var(--dsw-alias-fill-l2,rgba(127,127,127,.14));font-weight:500}
.nwc-nav-item:focus-visible{outline:2px solid var(--dsw-alias-border-focus,currentColor);outline-offset:-2px}
.nwc-nav-icon{width:16px;display:inline-flex;color:var(--dsw-alias-label-secondary)}
.nwc-count{margin-left:auto;font-size:11px;padding:0 6px;border-radius:9px;background:var(--dsw-alias-state-warning-primary,#d97706);color:#fff}
.nwc-main{display:flex;flex-direction:column;min-width:0;min-height:0;container:nwc-main/inline-size}
.nwc-top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 24px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-crumb{display:flex;align-items:baseline;gap:8px;color:var(--dsw-alias-label-secondary);min-width:0}
.nwc-crumb h1{font-size:18px;font-weight:500;margin:0;color:var(--dsw-alias-label-primary)}
.nwc-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.nwc-notice{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:12px 24px 0;padding:8px 12px;border:1px solid var(--dsw-alias-border-l2);border-radius:8px}
.nwc-notice>span{flex:1;min-width:0}
.nwc-notice>button{flex-shrink:0;white-space:nowrap}
.nwc-notice.is-error{border-color:var(--dsw-alias-state-error-primary);color:var(--dsw-alias-state-error-primary)}
.nwc-notice ul{margin:0;padding-left:18px}
.nwc-content .nwc-notice,.nwc-dialog-body .nwc-notice{margin:0}
.nwc-notice.is-warning{border-color:var(--dsw-alias-state-warning-primary)}
.nwc-notice.is-info{color:var(--dsw-alias-label-secondary)}
.nwc-content{flex:1;min-height:0;overflow:auto;padding:16px 24px 32px;display:flex;flex-direction:column;gap:14px}
.nwc-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:nowrap;min-height:36px}
.nwc-toolbar-end{display:flex;align-items:center;gap:8px;margin-left:auto;flex-shrink:0}
.nwc-row-menu{min-width:140px}
.nwc-pick-anchor{display:flex!important;width:100%}
.nwc-pick-menu{min-width:320px;width:max-content!important;max-width:min(560px,90vw)!important;max-height:360px!important;overflow-y:auto!important}
.nwc-pick-menu [role=menuitem]{height:auto!important;min-height:32px;align-items:center;white-space:normal!important}
.nwc-pick-menu [role=menuitem] *{white-space:normal!important;text-overflow:clip!important}
.nwc-opt{display:flex;flex-direction:column;gap:1px;min-width:0;padding:3px 0;overflow-wrap:anywhere}
.nwc-opt-sub{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-toolbar>.nwc-sub{flex:1;min-width:0}
.nwc-search{width:280px;max-width:100%}
.nwc-toolbar>.nwc-search{width:auto;flex:0 1 280px;min-width:160px}
.nwc-pills{display:flex;gap:6px;flex-wrap:nowrap;flex-shrink:0}
.nwc-card{border:1px solid var(--dsw-alias-border-l2);border-radius:10px;padding:4px 0;min-width:0}
.nwc-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 16px}
.nwc-card-head h3{margin:0;font-size:14px;font-weight:500}
.nwc-card>.nwc-text,.nwc-card>.nwc-empty{padding:0 16px 12px}
.nwc-card .nw-setting-section,.nwc-card .nw-connections,.nwc-card>.nw-setting-row{padding:0 16px}
.nwc-table-wrap{overflow:auto}
.nwc-table{width:100%;border-collapse:collapse;table-layout:fixed}
.nwc-table th{white-space:nowrap;text-align:left;font-weight:500;font-size:12px;color:var(--dsw-alias-label-secondary);padding:8px 12px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-table td{white-space:nowrap;overflow:hidden;padding:9px 12px;border-bottom:1px solid var(--dsw-alias-border-l2);vertical-align:middle}
.nwc-table tr:last-child td{border-bottom:none}
.nwc-cell{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.nwc-cell .nwc-sub,.nwc-cell .nwc-strong{max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nwc-cell .nwc-strong{display:block}
.nwc-cell .nwc-name-line{display:flex;align-items:center;gap:6px;min-width:0}
.nwc-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nwc-name-line>:not(.nwc-name){flex-shrink:0}
.nwc-table td.nwc-ops{overflow:visible}
.nwc-row-actions{display:flex;gap:2px;flex-wrap:nowrap}
.nwc-table .nwc-ops{position:sticky;right:0;background:var(--dsw-alias-bg-base,#fff);box-shadow:-1px 0 0 var(--dsw-alias-border-l2)}
.nwc-strong{font-weight:500;display:inline-flex;gap:6px;align-items:center}
.nwc-sub{font-size:12px;color:var(--dsw-alias-label-secondary);overflow:hidden;text-overflow:ellipsis}
.nwc-mono{font-family:var(--dsw-font-mono,ui-monospace,Menlo,monospace);font-size:12px}
.nwc-ellipsis{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:240px;display:inline-block;vertical-align:middle}
.nwc-empty{color:var(--dsw-alias-label-secondary);padding:14px 16px;margin:0}
.nwc-text{margin:0;line-height:1.6}
.nwc-error{color:var(--dsw-alias-state-error-primary);margin:8px 0 0}
.nwc-danger-text{color:var(--dsw-alias-state-error-primary)}
.nwc-danger-button{color:var(--dsw-alias-state-error-primary);border-color:var(--dsw-alias-state-error-primary)}
.nwc-stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}
@media(max-width:1100px){.nwc-stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
.nwc-stat{all:unset;box-sizing:border-box;border:1px solid var(--dsw-alias-border-l2);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:4px;cursor:pointer}
.nwc-stat:disabled{cursor:default}
.nwc-stat:not(:disabled):hover{background:var(--dsw-alias-fill-l1,rgba(127,127,127,.08))}
.nwc-stat-value{font-size:20px;font-weight:500}
.nwc-stat-label{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-tabs{display:flex}
.nwc-field{display:flex;flex-direction:column;gap:6px;min-width:0}
.nwc-field-label{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-field-hint{font-size:12px;color:var(--dsw-alias-label-secondary)}
.nwc-pick{justify-content:space-between;width:100%;max-width:100%}
.nwc-textarea{box-sizing:border-box;width:100%;resize:vertical;font:inherit;color:inherit;background:transparent;border:1px solid var(--dsw-alias-border-l2);border-radius:8px;padding:8px 10px;line-height:1.5}
.nwc-textarea:focus{outline:2px solid var(--dsw-alias-border-focus,currentColor);outline-offset:-1px}
.nwc-dialog-body{display:flex;flex-direction:column;gap:14px;max-height:min(62vh,640px);overflow:auto;overscroll-behavior:contain;padding:2px}
.nwc-dialog{width:min(560px,92vw)!important;max-width:92vw!important;max-height:100%}
.nwc-dialog-wide{width:min(820px,92vw)!important}
.nwc-dialog-content,.nwc-dialog-content>:last-child{min-height:0}
.nwc-diagnostic-list{display:grid;gap:8px;margin:0;padding-inline-start:20px;overflow-wrap:anywhere}
.nwc-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.nwc-form-grid-host{grid-template-columns:minmax(0,1fr) 140px}
.nwc-dialog-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%}
.nwc-pad{padding:0 16px 12px}
.nwc-form-section{display:flex;flex-direction:column;gap:12px;padding-bottom:16px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-form-section:last-of-type{border-bottom:none;padding-bottom:0}
.nwc-form-section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:28px}
.nwc-form-section-head h4{margin:0;font-size:13px;font-weight:500}
.nwc-operand{display:grid;grid-template-columns:110px repeat(4,minmax(0,1fr)) 56px;gap:10px;align-items:end}
.nwc-operand-notime{grid-template-columns:110px repeat(3,minmax(0,1fr)) 56px}
.nwc-operand-remove{display:flex;align-items:center;min-height:34px}
.nwc-check-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 14px;max-height:180px;overflow:auto}
.nwc-inline{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.nwc-list{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px}
.nwc-error-text{color:var(--dsw-alias-state-error-primary)}
.nwc-ok-text{color:var(--dsw-alias-state-success-primary,#16a34a)}
.nwc-footer-status{min-width:0;flex:1;flex-wrap:nowrap}
.nwc-footer-status>span{min-width:0;max-width:360px}
.nwc-field .nw-menu-anchor,.nwc-field .nw-menu-anchor>span{display:flex;width:100%}
.nwc-field .nwc-pick{width:100%;height:34px;justify-content:space-between}
.nwc-pick .nwc-ellipsis{flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nwc-pick svg{flex-shrink:0}
@media (max-width:900px){.nwc-operand,.nwc-operand-notime{grid-template-columns:1fr 1fr}}
.nwc-span{grid-column:1/-1}
.nwc-enum{display:flex;flex-direction:column;gap:8px}
.nwc-enum-row{display:grid;grid-template-columns:1fr auto 1fr auto;gap:8px;align-items:center}
.nwc-check-list{display:flex;flex-direction:column;max-height:46vh;overflow:auto;border:1px solid var(--dsw-alias-border-l2);border-radius:8px}
.nwc-check-row{display:flex;align-items:center;gap:12px;padding:6px 10px;border-bottom:1px solid var(--dsw-alias-border-l2)}
.nwc-check-row:last-child{border-bottom:none}
@container (max-width:860px){.nwc-nav{width:180px}.nwc-stats{grid-template-columns:repeat(3,minmax(0,1fr))}.nwc-form-grid{grid-template-columns:1fr}.nwc-top{flex-wrap:wrap}}
@container (max-width:680px){.nwc-nav{width:52px}.nwc-nav-title,.nwc-nav-item>span:not(.nwc-nav-icon):not(.nwc-count){display:none}.nwc-nav{padding:12px 6px}.nwc-brand>span{display:none}}
`;
