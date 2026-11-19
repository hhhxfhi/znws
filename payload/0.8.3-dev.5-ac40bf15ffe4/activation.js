import {randomUUID} from 'node:crypto';
import {redactDiagnostic} from './diagnostics.js';
import {readFileSync} from 'node:fs';
import {mkdir,writeFile,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {startEngine} from './runtime.js';
import {createTransportClient} from './packages/natural-data/index.js';

const HEALTH_TIMEOUT_MS=2500;

export function createActivation({dataRoot,python,signal,onReady=async()=>{}}) {
  const build=JSON.parse(readFileSync(new URL('./build-info.json',import.meta.url),'utf8'));
  const status={schema_version:1,version:build.version,build_id:build.build_id,ready:false,stage:'starting',attempts:0};
  let engine,transport,pending,stopping,contract,nextAttempt=0;
  const safe=error=>redactDiagnostic(error?.message||error).replace(/https?:\/\/\S+/g,'[endpoint]').slice(0,600);
  const save=async()=>{
    status.updated_at=new Date().toISOString();
    try{const dir=join(dataRoot,'runtime','diagnostics');await mkdir(dir,{recursive:true,mode:0o700});
      const file=join(dir,'activation.json'),temp=file+'.'+randomUUID()+'.tmp';await writeFile(temp,JSON.stringify(status,null,2),{mode:0o600,flag:'wx'});await rename(temp,file);
    }catch(error){console.error('[智能问数] 无法写入启动诊断：',safe(error));}
  };
  const unavailable=()=>Object.assign(new Error('ENGINE_NOT_READY: 问数服务尚未就绪（'+status.stage+'）：'+(status.error||'正在启动')+'。请稍后重试；诊断见 runtime/diagnostics/activation.json。'),{code:'ENGINE_NOT_READY',status:503});
  const release=async()=>{
    if(stopping)return stopping;
    transport?.dispose();transport=undefined;
    const prior=engine;engine=undefined;
    if(prior)stopping=prior.stop().finally(()=>{stopping=undefined;});
    await stopping;
  };
  async function start(){
    status.ready=false;status.attempts++;delete status.error;status.stage='engine';await save();
    try{
      engine=await startEngine({dataRoot,python,signal});
      transport=createTransportClient({...engine,signal});
      status.stage='contract';await save();
      for(let attempt=0;attempt<3;attempt++){
        try{
          const value=await transport.call('runtime_contracts','__operator__',{},'',AbortSignal.any([signal,AbortSignal.timeout(10000)]));
          if(value?.build_id!==build.build_id||!Array.isArray(value.capabilities))throw Object.assign(Error('运行合约与插件构建不一致。'),{code:'PLUGIN_CONTRACT_MISMATCH'});
          contract=value;break;
        }catch(error){
          signal.throwIfAborted();
          if(error.code==='PLUGIN_CONTRACT_MISMATCH'||[401,403].includes(error.status)||attempt===2)throw error;
          await delay([500,1500][attempt],undefined,{signal});
        }
      }
      signal.throwIfAborted();status.stage='ready';status.ready=true;delete status.error;delete status.code;
      await onReady();await save();return contract;
    }catch(error){
      status.ready=false;status.code=error.code||'ENGINE_NOT_READY';status.error=safe(error);nextAttempt=Date.now()+5000;
      await release();await save();throw unavailable();
    }
  }
  async function probeAndRecover(){
    const current=engine;
    // A delayed response from the owned child is not proof it exited. Give
    // inconclusive failures one more check before aborting the shared transport.
    for(let attempt=0;attempt<2&&current?.isAlive();attempt++){
      try{
        const response=await fetch(current.endpoint+'/health',{headers:{authorization:'Bearer '+current.token},
          signal:AbortSignal.any([signal,AbortSignal.timeout(HEALTH_TIMEOUT_MS)])});
        if(!response.ok){
          await response.body?.cancel();
          if([401,403].includes(response.status))break;
          continue;
        }
        const health=await response.json();
        if(typeof health?.build_id!=='string'||!health.build_id)continue;
        if(health.build_id===build.build_id&&current===engine&&current.isAlive())return contract;
        break;
      }catch{signal.throwIfAborted();}
    }
    signal.throwIfAborted();
    status.ready=false;status.stage='connection';contract=undefined;
    await release();
    return start();
  }
  function ensureReady({probe=false}={}){
    if(signal.aborted)return Promise.reject(signal.reason);
    if(pending)return pending;
    if(status.ready&&!probe)return Promise.resolve(contract);
    if(status.ready){pending=probeAndRecover().finally(()=>{pending=undefined;});return pending;}
    if(Date.now()<nextAttempt)return Promise.reject(unavailable());
    pending=start().finally(()=>{pending=undefined;});return pending;
  }
  return {
    get contract(){return contract;},
    status:()=>({...status}),ensureReady,
    setClientStatus(value){status.client=value;},
    async call(...args){
      if(args[0]==='runtime_status')return {...status};
      await ensureReady();
      const current=transport;
      try{return await current.call(...args);}catch(error){
        if(error.code==='ENGINE_DOWN'&&current===transport){
          status.ready=false;status.stage='connection';status.code='ENGINE_NOT_READY';status.error=safe(error);contract=undefined;
          await release();await save();throw unavailable();
        }
        throw error;
      }
    },
    async stop(){await pending?.catch(()=>{});status.ready=false;status.stage='stopped';contract=undefined;await release();}
  };
}
