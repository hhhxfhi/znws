import {readFileSync} from 'node:fs';
const runtimeKey='naturalDataRuntime_'+JSON.parse(readFileSync(new URL('./build-info.json',import.meta.url),'utf8')).build_id;
export const name='natural-data-unavailable';
export const inject=[runtimeKey,'agentPresets'];
export function apply(ctx){
  // The owning bundle restores and reassembles at system-prompt/assemble.
  // A placeholder reaching request means no safe tool assembly was possible.
  ctx.on('agent/request',async()=>{
    throw Object.assign(Error('ENGINE_NOT_READY: 问数工具尚未就绪，本次没有调用模型。'),{code:'ENGINE_NOT_READY'});
  },{prepend:true});
}
