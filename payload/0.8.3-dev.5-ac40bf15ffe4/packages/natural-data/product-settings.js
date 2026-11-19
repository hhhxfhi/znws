// Settings-only composition of the host's existing controls. No separate app shell.
export function createProductSettings({React,h,Input,Button,Checkbox,SettingRow,Detail,SettingsMenu,IconChevronDownOutlineMedium,request}){
  function Choice({label,value,options,onChange,disabled}){
    const [open,setOpen]=React.useState(false);
    return h('label',{className:'nw-field'},h('span',null,label),h(SettingsMenu,{open,onClose:()=>setOpen(false),selectedId:value,items:options,onSelect:id=>{onChange(id);setOpen(false);},anchor:h(Button,{variant:'outline',size:'sm',disabled,'aria-label':label,onClick:()=>setOpen(v=>!v)},options.find(x=>x.id===value)?.label||'请选择',h(IconChevronDownOutlineMedium,{size:16}))}));
  }
  function Field({label,value,onChange,disabled,...props}){
    return h('label',{className:'nw-field'},h('span',null,label),h(Input,{value,disabled,'aria-label':label,onChange:e=>onChange(e.target.value),...props}));
  }
  function OnboardingPanel({cfg,current,busy,dirty,onNavigate}){
    const status=current?.readiness,ready=status?.basic_query.state==='ready';
    return h(Detail,{title:'接入检查与使用引导 · '+(cfg.edition?cfg.edition+' ':'')+(cfg.plugin_version||'0.5.1'),initialOpen:!ready},
      h(SettingRow,{title:ready?'基础问数已就绪':'完成以下步骤即可基础问数',description:ready?'当前已发布的数据范围可以直接提问，无需预设指标。业务定义不明确时会先澄清。':'数据库连接、项目授权和发布分别检查；业务指标可以稍后按需配置。'}),
      h(SettingRow,{title:'1. 接入数据库',description:cfg.sources.length?'已登记 '+cfg.sources.length+' 个可见来源。':'尚未配置可见的数据源。'},cfg.system_admin&&h(Button,{size:'sm',variant:'outline',disabled:busy||dirty,onClick:()=>onNavigate('connections')},'管理数据库')),
      h(SettingRow,{title:'2. 授权并发布业务项目',description:current?(current.name+' · '+(current.published_version?'生效版本 v'+current.published_version:'尚未发布')):'先在下方创建业务项目，再选择数据源和授权表。'},current&&h(Button,{size:'sm',variant:'outline',disabled:busy||dirty,onClick:()=>onNavigate('sources')},'查看数据范围')),
      h(SettingRow,{title:'3. 新建会话开始提问',description:cfg.default_project?'新会话使用已关联或默认项目；已执行会话继续使用原项目。':'只有一个已授权发布项目时自动用于新会话；多个项目请先设置默认项目。'}),
      (status?.issues||[]).map((issue,i)=>h(SettingRow,{key:i,title:issue.blocking?'待完成':'提示',description:issue.message},h(Button,{size:'sm',variant:'outline',disabled:busy||dirty||!current,onClick:()=>onNavigate(issue.action)},issue.action==='business'?'配置业务口径':'查看并处理'))),
      (status?.sources||[]).flatMap(s=>s.unavailable_metrics.map((m,i)=>h(SettingRow,{key:s.id+':'+i,title:(m.name||m.id)+'暂不可用',description:m.reason},h(Button,{size:'sm',variant:'outline',disabled:busy||dirty,onClick:()=>onNavigate('business')},'修复指标')))),
      ready&&(status.sources||[]).flatMap(s=>s.starter_questions.map((q,i)=>h('p',{key:s.id+':q'+i,className:'nw-setting-description'},'可先提问：'+q.question))),
      cfg.environment&&h(Detail,{title:'运行环境检查'},h('p',{className:'nw-setting-description'},cfg.environment.model),cfg.environment.drivers.map(d=>h(SettingRow,{key:d.id,title:d.name,description:d.available?'驱动已就绪':'缺少驱动，请管理员安装插件运行依赖。'}))));
  }
  const emptyOperand=()=>({id:'value',table:'',column:'',aggregation:'sum',time_column:'',entity_columns:[],filters:[]});
  function MetricBuilder({project,draft,setDraft,sources,disabled,initial,onClose}){
    const [form,setForm]=React.useState(()=>{
      const row=initial,spec=row?.config;
      return {name:row?.name||'',definition:row?.definition||'',id:spec?.id||'',version:spec?.version||'1.0.0',unit:spec?.unit||'',source_id:spec?.source_id||draft.source_ids[0]||'',time_mode:spec?.time_mode||'period',coverage:(spec?.coverage||[]).join(', '),formula:spec?.formula||'value',operands:spec?.operands?.map(o=>({...o,column:o.column||'',time_column:o.time_column||''}))||[emptyOperand()],dimensions:spec?.dimensions||[],relations:spec?.relations||[],additive:spec?.additive||false};
    });
    const [error,setError]=React.useState('');
    const [observed,setObserved]=React.useState(null),[checking,setChecking]=React.useState(false);
    const mounted=React.useRef(true);React.useEffect(()=>()=>{mounted.current=false;},[]);
    const source=sources.find(s=>s.id===form.source_id),tables=(source?.tables||[]).filter(t=>(draft.tables[form.source_id]||[]).includes(t.name));
    const change=(key,value)=>setForm(f=>({...f,[key]:value}));
    const operand=(i,key,value)=>setForm(f=>({...f,operands:f.operands.map((o,j)=>j===i?{...o,[key]:value,...(key==='table'?{column:'',time_column:'',entity_columns:[]}:{} )}:o)}));
    async function inspectTime(){setChecking(true);setError('');setObserved(null);try{const value=await request('inspect_periods',{business_project:project,source_id:form.source_id,table:form.operands[0].table,column:form.operands[0].time_column});if(mounted.current)setObserved(value);}catch(e){if(mounted.current)setError(e.message);}finally{if(mounted.current)setChecking(false);}}
    function save(){
      try{
        if(!form.name.trim()||!form.definition.trim()||!form.unit.trim()||!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(form.id))throw Error('请填写名称、业务定义、单位和有效的指标标识（字母开头，可含数字和下划线）。');
        if(!/^\d+\.\d+\.\d+$/.test(form.version))throw Error('指标版本请使用三个数字，例如 1.0.0。');
        const coverage=form.time_mode==='all'?[]:form.coverage.split(/[,，\s]+/).filter(Boolean);
        if(form.time_mode==='period'&&(!coverage.length||coverage.some(p=>!/^20\d{2}(-(?:0[1-9]|1[0-2]))?$/.test(p))))throw Error('请填写已确认的期间，例如 2026 或 2026-10，以逗号分隔；完整年度需业务确认。');
        const operands=form.operands.map(o=>{
          const table=tables.find(t=>t.name===o.table);
          if(!table||!o.id||(!o.column&&o.aggregation!=='count')||(form.time_mode==='period'&&!o.time_column))throw Error('请为每个操作数选择授权表、字段和所需时间字段。');
          return {...o,column:o.column||null,time_column:form.time_mode==='all'?null:o.time_column};
        });
        const spec={kind:'semantic-metric',id:form.id,version:form.version,source_id:form.source_id,source_revision:source?.target_revision||0,unit:form.unit.trim(),definition:form.definition.trim(),time_mode:form.time_mode,coverage,operands,formula:form.formula,dimensions:form.dimensions,relations:form.relations,additive:form.additive};
        const row={id:initial?.id||crypto.randomUUID(),name:form.name.trim(),definition:form.definition.trim(),config:spec};
        setDraft(d=>({...d,terms:initial?d.terms.map(t=>t.id===initial.id?row:t):[...d.terms,row]}));onClose();
      }catch(e){setError(e.message);}
    }
    return h('div',{className:'nw-business-entry'},
      h('p',{className:'nw-setting-description'},'配置完成后添加到草稿，再保存并校验发布。字段候选来自授权目录，不自动认定业务口径；结构变化后可重新选择字段修复。'),
      h(Field,{label:'指标名称',value:form.name,disabled,onChange:v=>change('name',v)}),
      h(Field,{label:'业务定义与统计范围',value:form.definition,disabled,onChange:v=>change('definition',v)}),
      h(Field,{label:'指标标识',value:form.id,disabled,onChange:v=>change('id',v),placeholder:'例如 total_amount'}),
      h(Field,{label:'指标版本',value:form.version,disabled,onChange:v=>change('version',v)}),
      h(Field,{label:'指标单位',value:form.unit,disabled,onChange:v=>change('unit',v)}),
      h(Choice,{label:'指标数据源',value:form.source_id,disabled,options:sources.filter(s=>draft.source_ids.includes(s.id)).map(s=>({id:s.id,label:s.name})),onChange:v=>setForm(f=>({...f,source_id:v,operands:[emptyOperand()],dimensions:[],relations:[]}))}),
      h(Choice,{label:'统计时间方式',value:form.time_mode,disabled,options:[{id:'period',label:'按期间统计'},{id:'all',label:'全部授权数据（无期间）'}],onChange:v=>change('time_mode',v)}),
      form.time_mode==='period'&&h(Field,{label:'已确认可查询的期间',value:form.coverage,disabled,onChange:v=>change('coverage',v),placeholder:'例如 2025, 2026-09, 2026-10'}),
      form.operands.map((o,i)=>h(Detail,{key:i,title:'操作数 '+(i+1),initialOpen:true},
        h(Field,{label:'操作数标识 '+(i+1),value:o.id,disabled,onChange:v=>operand(i,'id',v)}),
        h(Choice,{label:'授权表 '+(i+1),value:o.table,disabled,options:tables.map(t=>({id:t.name,label:t.label?t.label+' · '+t.name:t.name})),onChange:v=>operand(i,'table',v)}),
        h(Choice,{label:'聚合方式 '+(i+1),value:o.aggregation,disabled,options:[['sum','合计'],['count','计数'],['distinct','不同值计数'],['avg','算术平均'],['min','最小值'],['max','最大值']].map(([id,label])=>({id,label})),onChange:v=>operand(i,'aggregation',v)}),
        h(Choice,{label:'统计字段 '+(i+1),value:o.column,disabled,options:[...(o.aggregation==='count'?[{id:'',label:'全部记录'}]:[]),...(tables.find(t=>t.name===o.table)?.columns||[]).map(c=>({id:c.name,label:c.label?c.label+' · '+c.name:c.name}))],onChange:v=>operand(i,'column',v)}),
        form.time_mode==='period'&&h(Choice,{label:'时间字段 '+(i+1),value:o.time_column,disabled,options:(tables.find(t=>t.name===o.table)?.columns||[]).map(c=>({id:c.name,label:c.name})),onChange:v=>operand(i,'time_column',v)}),
        form.operands.length>1&&h(Button,{variant:'ghost',size:'sm',disabled,onClick:()=>change('operands',form.operands.filter((_,j)=>j!==i))},'移除此操作数'))),
      h(Button,{variant:'outline',size:'sm',disabled:disabled||form.operands.length>=10,onClick:()=>change('operands',[...form.operands,{...emptyOperand(),id:'value'+(form.operands.length+1)}])},'添加操作数'),
      form.time_mode==='period'&&h(Button,{variant:'outline',size:'sm',disabled:disabled||checking||!form.operands[0]?.table||!form.operands[0]?.time_column,onClick:inspectTime},checking?'正在检查时间范围…':'检查首个时间字段的实际范围'),
      observed&&h('p',{role:'status',className:'nw-setting-description'},'最早值：'+(observed.first_observed||'无记录')+'；最晚值：'+(observed.latest_observed||'无记录')+'。'+observed.message),
      h(Field,{label:'计算公式',value:form.formula,disabled,onChange:v=>change('formula',v),placeholder:'value 或 spent / budget * 100'}),
      h('p',{className:'nw-setting-description'},'公式引用上方操作数标识，支持四则运算。比例、平均数及库存应按实际业务规则配置，零分母返回未知。跨表维度、筛选及唯一键关联可在添加后使用高级配置。'),
      tables.length>0&&h(Detail,{title:'可用分组字段'},tables.filter(t=>t.name===form.operands[0]?.table).flatMap(t=>(t.columns||[]).map(c=>h(Checkbox,{key:t.name+c.name,label:(c.label||c.name)+' · '+t.name,disabled,checked:form.dimensions.some(d=>d.table===t.name&&d.column===c.name),onChange:checked=>change('dimensions',checked?[...form.dimensions,{id:'dim'+(form.dimensions.length+1)+'_'+c.name.replace(/[^a-zA-Z0-9_]/g,'').slice(0,25),table:t.name,column:c.name}]:form.dimensions.filter(d=>d.table!==t.name||d.column!==c.name))})))),
      h(Checkbox,{label:'已确认该指标可以按所需范围累加',checked:form.additive,disabled,onChange:v=>change('additive',v)}),
      error&&h('p',{role:'alert',className:'nw-setting-status','data-error':true},error),
      h('div',{className:'nw-actions'},h(Button,{variant:'outline',size:'sm',disabled,onClick:save},initial?'更新草稿指标':'添加到草稿'),h(Button,{variant:'ghost',size:'sm',disabled,onClick:onClose},'取消配置')));
  }
  function TemplateTransfer({project,sources,busy,dirty,onImport}){
    const [raw,setRaw]=React.useState(''),[bindings,setBindings]=React.useState({}),[error,setError]=React.useState(''),[pending,setPending]=React.useState(false);
    let template;try{template=raw?JSON.parse(raw):null;}catch{}
    const active=React.useRef(true);React.useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
    async function exportConfig(){setPending(true);setError('');try{const value=await request('business_template',{business_project:project.id});if(!active.current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='ask-data-business-template.json';a.click();URL.revokeObjectURL(url);}catch(e){if(active.current)setError(e.message);}finally{if(active.current)setPending(false);}}
    async function file(event){const selected=event.target.files?.[0];if(!selected)return;if(selected.size>300000){setError('模板文件超过 300 KB。');return;}const text=await selected.text();if(active.current){setRaw(text);setBindings({});setError('');}}
    return h(Detail,{title:'业务模板导入与导出'},
      h('p',{className:'nw-setting-description'},'模板只包含业务规则及查询示例，不含连接凭据、用户授权或历史数据。请自行核对规则文字和 SQL 中的业务敏感信息。导入仅生成草稿，重新绑定当前已授权来源并校验发布后生效。表字段不同请先调整模板，结构校验失败不会保存。'),
      h(Button,{variant:'outline',size:'sm',disabled:busy||dirty||pending,onClick:exportConfig},'导出业务模板'),
      h('label',{className:'nw-field'},h('span',null,'选择业务模板文件'),h(Input,{type:'file',accept:'.json,application/json',disabled:busy||dirty,onChange:file,'aria-label':'选择业务模板文件'})),
      template?.sources?.map(s=>h(Choice,{key:s.alias,label:'模板来源 '+s.alias,value:bindings[s.alias]||'',disabled:busy||dirty,options:sources.filter(x=>project.draft.source_ids.includes(x.id)).map(x=>({id:x.id,label:x.name})),onChange:id=>setBindings(b=>({...b,[s.alias]:id}))})),
      raw&&!template&&h('p',{role:'alert'},'模板 JSON 无效。'),
      template&&h(Button,{variant:'outline',size:'sm',disabled:busy||dirty||!template.sources?.every(s=>bindings[s.alias]),onClick:()=>onImport(template,bindings)},'校验并导入草稿'),
      error&&h('p',{role:'alert'},error));
  }
  // 0.6.4: plugin-bundled business packs (same template format), matched to the project's authorized tables.
  function PackImport({project,busy,dirty,onImport}){
    const [packs,setPacks]=React.useState(null),[choice,setChoice]=React.useState({}),[error,setError]=React.useState('');
    const key=project.id+':'+(project.draft?.source_ids||[]).join(',')+':'+JSON.stringify(project.draft?.tables||{}).length;
    React.useEffect(()=>{let live=true;request('business_packs',{business_project:project.id}).then(v=>{if(live){setPacks(v.packs||[]);setError('');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[key]);
    if(!packs?.length&&!error)return null;
    return h(Detail,{title:'内置业务口径包'},
      h('p',{className:'nw-setting-description'},'插件内置的业务口径包，按当前项目已授权的表自动匹配，导入字段中文名、取值、口径说明、结构化指标和查询示例。库里缺少的表或字段会跳过并列出。导入只生成草稿，核对后校验发布；重复导入只替换包内条目，改过的条目保留。'),
      (packs||[]).map(p=>{const sid=choice[p.id]||p.matches[0]?.source_id||'';const m=p.matches.find(x=>x.source_id===sid);
        return h(SettingRow,{key:p.id,title:p.name+' · v'+p.version,description:p.description+' 共 '+p.tables+' 张表、'+p.metrics+' 个结构化指标、'+p.terms+' 条口径说明、'+p.examples+' 个查询示例。'+(m?'当前匹配 '+m.matched_tables+' 张表'+(m.missing_tables.length?'，缺少 '+m.missing_tables.length+' 张':'')+'。':'当前项目没有匹配的授权表。')+(p.imported_rows?'已导入。':'')},
          p.matches.length>1&&h(Choice,{label:'数据源',value:sid,options:p.matches.map(x=>({id:x.source_id,label:x.name+'（'+x.matched_tables+' 张表）'})),disabled:busy||dirty,onChange:id=>setChoice(c=>({...c,[p.id]:id}))}),
          h(Button,{variant:'outline',size:'sm',disabled:busy||dirty||!sid,onClick:()=>onImport(p.id,sid)},p.imported_rows?'重新导入草稿':'导入到草稿'));}),
      (packs||[]).some(p=>p.pending?.length)&&h(Detail,{title:'待业务确认的口径'},(packs||[]).flatMap(p=>p.pending.map((x,i)=>h('p',{key:p.id+':'+i,className:'nw-setting-description'},(i+1)+'. '+x)))),
      error&&h('p',{role:'alert'},error));
  }
  return {OnboardingPanel,MetricBuilder,TemplateTransfer,PackImport};
}
