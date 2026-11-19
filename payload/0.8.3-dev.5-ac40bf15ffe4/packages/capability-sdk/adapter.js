// Shared Harness adapter. It has no data/SQL/chart dependencies.
// Some model transports double-encode containers. Decode only where the active
// schema requires a container, then run the unchanged native validator. Never
// coerce scalar values, omit unknown keys, or mutate the recorded arguments.
import {parameterSchemaSpecToJsonSchema,validateJsonSchemaValue,ToolArgsError} from '@deepseek-ai/dsh-tools';
export function repairContainers(value,schema,definitions=schema.$defs||{}){
  const node=schema.$ref?definitions[schema.$ref.split('/').pop()]:schema;
  if(!node)return value;
  if(node.oneOf){
    const candidates=node.oneOf.map(branch=>({branch,value:repairContainers(value,branch,definitions)}))
      .filter(candidate=>validateJsonSchemaValue(toolInputSchema({...candidate.branch,$defs:definitions}),candidate.value,'').length===0);
    return candidates.length===1?candidates[0].value:value;
  }
  if(node.anyOf){
    const branches=node.anyOf.filter(x=>x.type!=='null');
    return branches.length===1?repairContainers(value,branches[0],definitions):value;
  }
  if(typeof value==='string'&&value.length<=100000&&['object','array'].includes(node.type)){
    try{
      const parsed=JSON.parse(value);
      if(node.type==='array'?Array.isArray(parsed):parsed!==null&&typeof parsed==='object'&&!Array.isArray(parsed))value=parsed;
    }catch{}
  }
  if(Array.isArray(value)&&node.type==='array'&&node.items)return value.map(v=>repairContainers(v,node.items,definitions));
  if(value!==null&&typeof value==='object'&&!Array.isArray(value)&&node.type==='object')
    return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,node.properties?.[k]?repairContainers(v,node.properties[k],definitions):v]));
  return value;
}

export function toolParameters(schema) {
  const definitions=schema.$defs||{};
  function convert(raw,required=false){
    const node=raw.$ref?definitions[raw.$ref.split('/').pop()]:raw;
    if(!node)throw Error('Unresolved capability schema reference');
    if(node.anyOf){
      const branches=node.anyOf.filter(x=>x.type!=='null');
      if(branches.length!==1)throw Error('Unsupported union in Harness capability schema');
      return {...convert(branches[0],required),...(node.description?{description:node.description}:{})};
    }
    const value={type:node.type,...(required?{required:true}:{}),...(node.description?{description:node.description}:{}),...(node.enum?{enum:node.enum}:{}),...(Object.hasOwn(node,'const')?{const:node.const}:{})};
    if(node.properties)value.properties=Object.fromEntries(Object.entries(node.properties).map(([k,v])=>[k,convert(v,(node.required||[]).includes(k))]));
    if(node.additionalProperties===false)value.additionalProperties=false;
    if(node.items)value.items=convert(node.items);
    return value;
  }
  return convert(schema).properties;
}

// Root choices are supported by the pinned native SDK. Keep each complete
// object branch instead of discarding cross-field constraints in a property map.
export function toolInputSchema(schema){
  if(schema.oneOf)return {oneOf:schema.oneOf.map(branch=>toolInputSchema({...branch,$defs:schema.$defs||{}}))};
  const value=parameterSchemaSpecToJsonSchema(toolParameters(schema));
  if(schema.additionalProperties===false)value.additionalProperties=false;
  return value;
}

function toolParameterSuperset(schema){
  if(!schema.oneOf)return toolParameters(schema);
  const branches=schema.oneOf;
  return toolParameters({type:'object',$defs:schema.$defs||{},
    properties:Object.assign({},...branches.map(branch=>branch.properties)),
    required:(branches[0].required||[]).filter(key=>branches.every(branch=>(branch.required||[]).includes(key)))});
}

export function registerCapability(ctx,{manifest,prompt,defineTool,invoke,context,decorate,cancel,normalizeArgs}){
  if(manifest.contract_version!==1)throw Error('Unsupported capability contract');
  ctx.systemPrompt.section({name:'natural-work:'+manifest.id,order:10500,text:prompt});
  for(const operation of manifest.operations){
    if(!operation.permission.endsWith('.read'))throw Error('External writes require an explicit Harness approval adapter');
    const inputSchema=toolInputSchema(operation.input_schema);
    const tool=defineTool({
      name:manifest.id+'_'+operation.name,
      description:operation.description,
      parameters:toolParameterSuperset(operation.input_schema),
      output:{schema:{type:'object',additionalProperties:false,properties:{payload:{type:'string',required:true}}},render:(_args,value)=>[{type:'text',text:value.payload}]},
      timeoutMs:operation.timeout_ms,
      presentCall:()=>({card:'generic',kind:'read',title:manifest.title+' · '+operation.title}),
      async execute(args,exec){
        const scope=context(exec);
        const abort=()=>cancel?.(operation.name,scope,args);
        if(exec.signal.aborted)throw Error('调用已取消');
        exec.signal.addEventListener('abort',abort,{once:true});
        try{
          const value=await invoke(operation.name,args,scope,exec.signal);
          return {payload:JSON.stringify(decorate?decorate(operation.name,value,scope):value)};
        }finally{exec.signal.removeEventListener('abort',abort);}
      }
    });
    ctx.tools.register({...tool,parameters:inputSchema,execute(args,exec){
      const parsed=repairContainers(args,operation.input_schema);
      const repaired=normalizeArgs ? normalizeArgs(operation.name,parsed) : parsed;
      const violations=validateJsonSchemaValue(inputSchema,repaired,'');
      if(violations.length)throw new ToolArgsError(violations);
      return tool.execute(repaired,exec);
    }});
  }
}
