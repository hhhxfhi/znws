import {readAnswerQuality} from './answer-guard.js';
import {userInfo} from 'node:os';
import { readFileSync, statSync, mkdirSync } from 'node:fs';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {dshHomePath} from '@deepseek-ai/dsh-home-paths';
import {StringDecoder} from 'node:string_decoder';
import {normalizeBasePath} from './public-path.js';
import {sessionTurnReview} from './session-review.js';

const staticAssetCache = new Map();
function etagMatches(header, etag) {
  if (!header) return false;
  const trimmed = String(header).trim();
  if (trimmed === '*') return true;
  const stripWeak = value => value.trim().replace(/^W\//, '');
  const target = stripWeak(etag);
  return trimmed.split(',').some(part => stripWeak(part) === target);
}
function sendStaticAsset(req, res, fileUrl, contentType) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {res.writeHead(405);res.end();return;}
  let stat;
  try {stat = statSync(fileUrl);} catch (err) {
    if (err?.code === 'ENOENT') {res.writeHead(404);res.end();return;}
    throw err;
  }
  const key = fileUrl.href;
  const mtimeSec = Math.floor(stat.mtimeMs / 1000);
  let entry = staticAssetCache.get(key);
  if (!entry || entry.size !== stat.size || entry.mtimeMs !== stat.mtimeMs) {
    const body = readFileSync(fileUrl);
    const etag = '"' + createHash('sha256').update(body).digest('hex').slice(0, 16) + '"';
    entry = {body, etag, size: stat.size, mtimeMs: stat.mtimeMs, mtimeSec, lastModified: new Date(mtimeSec * 1000).toUTCString()};
    staticAssetCache.set(key, entry);
  }
  res.setHeader('Content-Type', contentType);
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('ETag', entry.etag);
  res.setHeader('Last-Modified', entry.lastModified);
  const ifNoneMatch = req.headers?.['if-none-match'];
  const ifModifiedSince = req.headers?.['if-modified-since'];
  let notModified = false;
  if (ifNoneMatch) notModified = etagMatches(ifNoneMatch, entry.etag);
  else if (ifModifiedSince) {
    const parsed = Date.parse(ifModifiedSince);
    notModified = Number.isFinite(parsed) && Math.floor(parsed / 1000) >= entry.mtimeSec;
  }
  if (notModified) {res.writeHead(304);res.end();return;}
  if (req.method === 'HEAD') {res.writeHead(200);res.end();return;}
  res.end(entry.body);
}

export const name = 'natural-data';
export const inject = ['webServer', 'connection', 'sessionController', 'workspaceRegistry'];
let endpoint = 'http://127.0.0.1:5671';
let token = process.env.NATURAL_DATA_TOKEN;
let lifetime = new AbortController();
delete process.env.NATURAL_DATA_TOKEN;

// A packaged plugin owns its transport; the legacy launcher can still supply it.
export function configureTransport(value) {
  if (value?.endpoint) engineFailure = '';
  lifetime.abort();
  lifetime = new AbortController();
  if (!value.endpoint) lifetime.abort();
  endpoint = value.endpoint;
  token = value.token;
}

// 0.6.3: when the Python data engine is not running, every request says so plainly,
// with the reason recorded at start-up and what to do, instead of "fetch failed".
let engineFailure = '';
export function reportEngineFailure(error) {engineFailure = String(error?.message || error || '').slice(0, 300);}
export function engineDownMessage() {
  return '数据引擎未启动' + (engineFailure ? '（原因：' + engineFailure + '）' : '') +
    '。处理办法：使用 Python 3.11 或 3.12，运行插件目录 setup.py 准备锁定依赖后重启服务；详细原因见插件私有数据目录 runtime/diagnostics 中的日志。';
}
function engineDown(transport) {const error = new Error(transport ? '数据引擎未启动'+(transport.failure?'（'+transport.failure+'）':'')+'。请查看插件运行诊断。' : engineDownMessage()); error.status = 503; error.code = 'ENGINE_DOWN'; return error;}

export function createTransportClient(value={}) {
  const lifetime=new AbortController();
  if(value.signal?.aborted)lifetime.abort();
  else value.signal?.addEventListener('abort',()=>lifetime.abort(),{once:true});
  const transport={endpoint:value.endpoint,token:value.token,lifetime,failure:''};
  return {call:(...args)=>invokeTransport(transport,...args),dispose:()=>lifetime.abort()};
}
export function call(...args) {return invokeTransport({endpoint,token,lifetime},...args);}

async function invokeTransport(transport, operation, project, args={}, request_key='', signal, session_id='', host_evidence={}) {
  const {endpoint,token,lifetime}=transport;
  if (!endpoint || !token) throw engineDown(transport);
  let response;
  try {
  response = await fetch(`${endpoint}/v1/${operation}`, {method:'POST', headers:{'content-type':'application/json', authorization:`Bearer ${token}`}, body:JSON.stringify({project,args,request_key,session_id,host_evidence,principal:process.env.NATURAL_WORK_PRINCIPAL||'local:'+userInfo().username}), signal:signal ? AbortSignal.any([signal,lifetime.signal]) : lifetime.signal});
  } catch (error) {
    if (error?.name === 'AbortError' || signal?.aborted) throw error;
    // Connection refused or reset: the engine process is gone.
    transport.failure = '数据服务没有响应：' + String(error?.cause?.code || error?.message || error);
    throw engineDown(transport);
  }
  if (!response.ok) {
    let error = {};
    if ((response.headers.get('content-type')||'').includes('application/json')) {
      try {error = await response.json();} catch { /* Non-JSON upstream failure. */ }
    } else {await response.body?.cancel();}
    if (!error || typeof error !== 'object') error = {};
    const evidence=error.coverage_evidence?.schema_version===1?'\nCOVERAGE_EVIDENCE: '+JSON.stringify(error.coverage_evidence):'';
    const failure=new Error((typeof error.detail==='string' ? error.detail : '数据服务返回异常响应（HTTP '+response.status+'），请检查私有诊断日志。')+evidence);failure.status=response.status;throw failure;
  }
  return operation === 'export' ? response.text() : response.json();
}
export function apply(ctx, config = {}) {
  const invoke=config.call||call;
  const publicBasePath = normalizeBasePath(config.publicBasePath ?? process.env.NATURAL_DATA_PUBLIC_BASE_PATH ?? '');
  const handler = async (req,res) => {
    const rejection=ctx.connection.requestRejection({headers:req.headers});
    if(rejection){res.writeHead(typeof rejection === 'number' ? rejection : rejection.status || 401);res.end('Unauthorized');return;}
    const u = new URL(req.url,'http://localhost');
    if (publicBasePath && u.pathname.startsWith(publicBasePath + '/natural-data/')) {
      u.pathname = u.pathname.slice(publicBasePath.length);
    }
    try {
      if (u.pathname.startsWith('/natural-data/assets/')) {
        const file=u.pathname.slice('/natural-data/assets/'.length);
        if(!/^[a-zA-Z0-9_.-]+$/.test(file)){res.writeHead(404);res.end();return;}
        const extension=file.split('.').pop();
        const type=({css:'text/css',js:'application/javascript',woff:'font/woff',woff2:'font/woff2',ttf:'font/ttf',svg:'image/svg+xml',png:'image/png'})[extension]||'application/octet-stream';
        sendStaticAsset(req,res,new URL('./assets/'+file,import.meta.url),type);return;
      }
      if (u.pathname === '/natural-data/client.css') {sendStaticAsset(req,res,new URL('./style.css',import.meta.url),'text/css');return;}
      if (u.pathname === '/natural-data/view') {
        if (!u.searchParams.has('settings') && !u.searchParams.has('result_ref')) {res.writeHead(404);res.end('请在对话中使用问数模式。');return;}
        res.setHeader('Cache-Control','no-store');
        res.setHeader('Content-Type','text/html; charset=utf-8');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'");
        res.end('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>问数结果 · 自然 Work</title><link rel="stylesheet" href="./assets/result-app.css"></head><body><div id="root"></div><script type="module" src="./assets/result-app.js"></script></body></html>');return;
      }
      if (u.pathname === '/natural-data/viewer.js') {sendStaticAsset(req,res,new URL('./viewer.js',import.meta.url),'application/javascript');return;}
      if(u.pathname==='/natural-data/api/answer-quality'){
        if(req.method!=='GET'){res.writeHead(405);res.end();return;}
        const value=readAnswerQuality(u.searchParams.get('session'),Number(u.searchParams.get('turn')),config.answerQualityDirectory);
        res.setHeader('Cache-Control','no-store');res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));return;
      }
      const requestedOp = u.pathname.split('/').pop();
      const op = ['admin_trial','admin_sessions','admin_session_review','admin_trial_review'].includes(requestedOp)?'admin_validation':requestedOp;
      if(!['runtime_status','config','catalog','results','result','visualize','export','bind','test','policy','policy_update','business_update','business_template','business_packs','excel_template','excel_import','excel_history','inspect_periods','session_project','project_skills','connections','connection_test','connection_update','make_report','edit_report','admin_check','admin_validation'].includes(op)) {res.writeHead(404);res.end();return;}
      if(req.method!=='GET' && req.method!=='POST') {res.writeHead(405);res.end();return;}
      if(['bind','visualize','test','policy_update','business_update','excel_import','connection_test','connection_update','make_report','edit_report','admin_check','admin_validation'].includes(op) && req.method!=='POST') {res.writeHead(405);res.end();return;}
      if(['connections','connection_test','connection_update','make_report','edit_report','admin_check','admin_validation'].includes(op) && u.search) {res.writeHead(422);res.end('连接配置必须放在请求正文中。');return;}
      if(op==='session_project' && req.method==='GET' && u.searchParams.has('business_project')) {res.writeHead(405);res.end();return;}
      let body={};
      if(req.method==='POST') {
        if (!/^application\/json(?:\s*;|\s*$)/i.test(req.headers['content-type']||'')) {
          res.writeHead(415,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'请求必须使用 application/json。'}));return;
        }
        // HTTP chunks can split a Chinese character. Decode incrementally instead of
        // coercing each Buffer to text, which silently saves replacement characters.
        const decoder=new StringDecoder('utf8');let raw='',bytes=0;
        // XLSX is sent as base64: allow 5 MiB plus JSON overhead only on its endpoint.
        const limit=op==='excel_import'?Math.ceil(5*1048576/3)*4+65536:524288;
        const tooLarge=()=>new Error(op==='excel_import'?'Excel 请求过大，请上传不超过 5 MB 的文件。':'请求过大');
        for await(const part of req){bytes+=part.length;if(bytes>limit)throw tooLarge();raw+=decoder.write(part);}
        raw+=decoder.end();if(raw.length>limit)throw tooLarge();
        body=JSON.parse(raw||'{}');
      }
      const project = body.project || u.searchParams.get('project') || '__operator__';
      const args={...Object.fromEntries(u.searchParams),...body};delete args.project;
      let value;
      if(requestedOp==='admin_trial'){
        const trial=await invoke('admin_validation',project,{...args,action:'prepare_trial'});
        const workspacePath=join(config.dataRoot||dshHomePath('plugin-data','ask-data'),'validation-workspace');
        mkdirSync(workspacePath,{recursive:true,mode:0o700});
        const workspace=await ctx.workspaceRegistry.create(workspacePath,'配置代表问题验证');
        const signal=AbortSignal.timeout(30000);
        const session=await ctx.sessionController.create({workspaceId:workspace.id,agentPreset:'natural-data'},signal);
        await invoke('session_project',workspacePath,{session_id:session.sessionId,business_project:trial.business_project});
        const started=await invoke('admin_validation',project,{...args,action:'start_trial',session_id:session.sessionId},'',signal,'',
          {trial_session:{session_id:session.sessionId,workspace:workspacePath}});
        await ctx.sessionController.prompt({sessionId:session.sessionId,requestId:'configuration-trial-'+session.sessionId,
          mode:'queue',content:[{type:'text',text:trial.question}]},signal);
        value={sessionId:session.sessionId,record:started.record,config_fingerprint:trial.config_fingerprint,state:'试问已提交，等待结果核对'};
      }else if(requestedOp==='admin_sessions'){
        const allowed=await invoke(op,project,{...args,action:'review_sessions'});
        const sessions=[];
        for(const id of allowed.session_ids.slice(-20).reverse())try{
          const inspected=await ctx.sessionController.inspect(id,AbortSignal.timeout(10000));
          const turns=inspected.events.filter(e=>e.type==='turn/start').slice(-10);
          for(const turn of turns)try{const row=sessionTurnReview(inspected,id,turn.data.turn);sessions.push({session_id:id,turn:row.turn,question:row.question});}catch{}
        }catch{/* Deleted or unavailable sessions are not review candidates. */}
        value={sessions};
      }else if(['admin_session_review','admin_trial_review'].includes(requestedOp)){
        await invoke(op,project,{...args,action:'authorize_session'});
        const inspected=await ctx.sessionController.inspect(args.session_id,AbortSignal.timeout(10000));
        const review=sessionTurnReview(inspected,args.session_id,args.turn);
        const quality=readAnswerQuality(args.session_id,review.turn,config.answerQualityDirectory);
        value=await invoke(op,project,{...args,action:requestedOp==='admin_trial_review'?'review_trial':'capture_review'},'',undefined,'',
          {session_review:review,answer_quality:quality});
      }else value=await invoke(op,project,args);
      res.setHeader('Cache-Control','no-store');
      if(op==='export'){res.setHeader('Content-Type','text/csv; charset=utf-8');res.setHeader('Content-Disposition','attachment; filename="natural-work-result.csv"');res.end(value);}
      else {
        const body=JSON.stringify(value);
        if(op==='result'){
          // invoke() above has already checked current identity, grants and
          // publication. Only then may an unchanged cached body be reused.
          const etag='"'+createHash('sha256').update(body).digest('hex')+'"';
          res.setHeader('ETag',etag);
          if(etagMatches(req.headers['if-none-match'],etag)){
            res.writeHead(304);res.end();return;
          }
        }
        res.setHeader('Content-Type','application/json');res.end(body);
      }
    } catch(error) {res.writeHead([401,403,409,422,500,502,503].includes(error.status)?error.status:422,{'Content-Type':'application/json'});res.end(JSON.stringify({error:error.message,...(error.code==='ENGINE_DOWN'?{engine_down:true}:{})}));}
  };
  for (const path of new Set(['/natural-data', publicBasePath + '/natural-data'])) {
    ctx.effect(() => ctx.webServer.register({kind:'prefix',path,handler}));
  }
}
