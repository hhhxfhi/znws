import {constants,openSync,writeSync,closeSync,fchmodSync,renameSync} from 'node:fs';
import {mkdir,chmod} from 'node:fs/promises';
import {join} from 'node:path';
import {StringDecoder} from 'node:string_decoder';

export function redactDiagnostic(value, secrets=[]) {
  let text=String(value).replace(/\x1b\[[0-9;]*m/g,'');
  for(const secret of secrets) if(secret)text=text.split(secret).join('[redacted]');
  if (/authorization|password|api[_-]?key|access[_-]?token|client[_-]?secret/i.test(text)) return '[sensitive diagnostic line redacted]';
  return text.replace(/([a-z][a-z0-9+.-]*:\/\/)[^\s/@]+:[^\s/@]+@/gi,'$1[redacted]@')
    .replace(/Bearer\s+\S+/gi,'Bearer [redacted]').slice(0,4096);
}

export async function diagnostics(dataRoot, name, secrets=[]) {
  const directory=join(dataRoot,'runtime','diagnostics');
  await mkdir(directory,{recursive:true,mode:0o700});await chmod(directory,0o700);
  const path=join(directory,name+'.log');
  try{renameSync(path,path+'.previous');}catch(error){if(error.code!=='ENOENT')throw error;}
  const fd=openSync(path,constants.O_CREAT|constants.O_WRONLY|constants.O_EXCL|(constants.O_NOFOLLOW||0),0o600);
  fchmodSync(fd,0o600);
  let written=0,closed=false;
  const write=line=>{if(closed||written>=262144)return;const safe=Buffer.from(redactDiagnostic(line,secrets)+'\n');const count=Math.min(safe.length,262144-written);writeSync(fd,safe,0,count);written+=count;};
  return {path,write,attach(child){
    for(const stream of [child.stdout,child.stderr]){
      const decoder=new StringDecoder('utf8');let pending='',discard=false;
      stream?.on('data',chunk=>{for(const part of decoder.write(chunk).split(/(?<=\n)/)){
        if(!discard)pending+=part;
        if(pending.length>8192){write(pending);pending='';discard=true;}
        if(part.endsWith('\n')){if(!discard)write(pending.trimEnd());pending='';discard=false;}
      }});
      stream?.on('end',()=>{if(!discard&&pending)write(pending+decoder.end());});
    }
    child.once('close',()=>{if(!closed){closed=true;closeSync(fd);}});
  },close(){if(!closed){closed=true;closeSync(fd);}}};
}
