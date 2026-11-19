import {createPublicUrls} from './public-path.js';
import {createProductSettings} from './product-settings.js';
import {createAdministration,administrationCss} from './administration.js';
import {createConsole,consoleCss} from './admin-console.js';
import {pickAnswerCards} from './answer-cards.js';
import {isExploratory} from './presentation-intent.js';
import {forecastClaimsByResult,resultEvents,resultRequestEvents} from './result-events.js';
import {capResultFrameHeight,mergeViewPatch,resultDisplayTitle,resultFrameHeight,resultFrameMessage} from './result-view.js';
import {publicMetricLabel} from './calculation-presentation.js';
const publicUrls = createPublicUrls(globalThis);
window.__ModuleLoader__.load({id:'@natural-work/ask-data',factory:(require)=>{
  const React=require('react'); const h=React.createElement;
  // Use the host's component implementations, icon artwork and theme tokens.
  const UI=require('@deepseek-ai/dsh-client-ui-primitives');
  const {Button,Switch,Checkbox,Input,Menu,DisclosureRow,SegmentedControl,IconDataOutlineMedium,IconChevronDownOutlineMedium}=UI;
  // Each activation owns its requests, including settings and result views.
  let lifetime = new AbortController();
  let openAdministration=null;
  let openNativeSession=null;
  async function fetch(url, options = {}) {
    const owner = lifetime;
    owner.signal.throwIfAborted();
    const request = new AbortController();
    const abort = () => request.abort();
    owner.signal.addEventListener('abort', abort, {once:true});
    options.signal?.addEventListener('abort', abort, {once:true});
    if (options.signal?.aborted) abort();
    try {
      const response = await globalThis.fetch(url, {...options, signal:request.signal});
      // Keep the lifetime subscription until the body has been consumed.
      for (const method of ['json','text','blob','arrayBuffer']) {
        const read = response[method].bind(response);
        response[method] = async () => {try {const value=await read();owner.signal.throwIfAborted();return value;} finally {release();}};
      }
      return response;
    } catch (error) {release();throw error;}
    function release(){owner.signal.removeEventListener('abort',abort);options.signal?.removeEventListener('abort',abort);}
  }
  const inject=['slots','uiConversation','sessions','layout'];
  // One persistent result viewport. Selection never clears data while loading.
  class ViewController {
    constructor(io){this.io=io;this.current=null;this.pending=false;this.generation=0;}
    accept(value,patch){if(this.current&&value.result_ref===this.current.result_ref&&(value.view_revision||0)<(this.current.view_revision||0))return false;this.current=value;this.io.accept(value,patch);return true;}
    async select(kind){
      if(this.pending||!this.current)return false;
      this.pending=true;
      const generation=++this.generation, resultRef=this.current.result_ref;
      const active=()=>generation===this.generation&&this.current?.result_ref===resultRef;
      let committed=false;
      this.io.busy(true);this.io.error('');
      try {
        const patch=await this.io.update(kind,this.current.view_revision);
        committed=true;
        if(!active())return false;
        const updated=mergeViewPatch(this.current,patch);
        const value=updated||await this.io.load();
        if(!active()||value.result_ref!==resultRef||!this.accept(value,updated?patch:undefined))return false;
        this.io.selected?.();
        return true;
      } catch(error) {
        if(active()){
          this.io.error(error.message);
          if(committed||error.status===409)try{
            const value=await this.io.load();
            if(active()&&value.result_ref===resultRef)this.accept(value);
          }catch{}
        }
        return false;
      } finally {
        if(generation===this.generation){this.pending=false;this.io.busy(false);}
      }
    }
    dispose(){this.generation++;this.pending=false;}
  }

  // Retry only reading a saved result. Never replay queries or mutations.
  const RESULT_CACHE_LIMIT=128;
  const resultCache=new Map();
  const resultCacheEtags=new WeakMap();
  const resultMutationMarks=new Map();
  const CACHE_INVALIDATE_MUTATIONS=new Set(['business_update','policy_update','connection_update','bind','make_report','edit_report','excel_import']);
  function queryParam(query,key){
    if(!query)return '';
    const q=String(query);
    if(typeof URLSearchParams==='function')return new URLSearchParams(q).get(key)||'';
    return new URL(q.startsWith('?')?q:'?'+q,'http://localhost').searchParams.get(key)||'';
  }
  function resultCacheKey(query){
    if(!query)return '';
    const ref=queryParam(query,'result_ref');
    if(!ref)return String(query);
    return `${queryParam(query,'project')}::${ref}`;
  }
  function cloneResultSnapshot(value){
    if(value===null||typeof value!=='object')return value;
    if(typeof structuredClone==='function')return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }
  function cacheResultEntry(cache,key,value,etag=null){
    if(!cache||!key||value===undefined)return;
    if(cache.has(key))cache.delete(key);
    else while(cache.size>=RESULT_CACHE_LIMIT){
      const oldest=cache.keys().next().value;
      if(oldest===undefined)break;
      cache.delete(oldest);
    }
    const snapshot=cloneResultSnapshot(value);
    cache.set(key,snapshot);
    if(etag)resultCacheEtags.set(snapshot,etag);
  }
  function invalidateResultRef(ref){
    if(!ref)return;
    const suffix=`::${ref}`;
    for(const key of [...resultCache.keys()]){
      if(key===ref||key.endsWith(suffix))resultCache.delete(key);
    }
    resultCache.delete(resultCacheKey(ref));
  }
  function frameHeightStorageKey(query){
    if(!query)return '';
    const ref=queryParam(query,'result_ref');
    return ref?`nw:fh:${queryParam(query,'project')}::${ref}:chart`:'';
  }
  function readFrameHeight(query){
    const key=frameHeightStorageKey(query);
    if(!key||typeof sessionStorage==='undefined')return 280;
    try{
      const raw=Number(sessionStorage.getItem(key));
      // 0.8.2: a restored height obeys the same chat-card cap as live measurement.
      return Number.isFinite(raw)&&raw>=40&&raw<=1200?capResultFrameHeight(Math.round(raw)):280;
    }catch{return 280;}
  }
  function writeFrameHeight(query,height){
    const key=frameHeightStorageKey(query);
    if(!key||typeof sessionStorage==='undefined')return;
    const n=Math.round(Number(height));
    if(!Number.isFinite(n)||n<40||n>1200)return;
    try{sessionStorage.setItem(key,String(n));}catch{}
  }
  function createFrameGate(limit=2){
    let active=0;const queue=[];
    const pump=()=>{
      while(active<limit&&queue.length){
        const next=queue.shift();
        if(next.aborted)continue;
        active++;
        next.grant();
      }
    };
    return {
      acquire(signal){
        if(signal?.aborted)return Promise.reject(Object.assign(Error('Aborted'),{name:'AbortError'}));
        return new Promise((resolve,reject)=>{
          let settled=false,released=false,onAbort=null;
          const cleanup=()=>{if(onAbort&&signal)signal.removeEventListener('abort',onAbort);};
          const release=()=>{
            if(released)return;
            released=true;
            cleanup();
            active=Math.max(0,active-1);
            pump();
          };
          const item={aborted:false,grant:()=>{settled=true;cleanup();resolve(release);}};
          onAbort=()=>{
            item.aborted=true;
            if(!settled){settled=true;cleanup();reject(Object.assign(Error('Aborted'),{name:'AbortError'}));}
          };
          if(signal)signal.addEventListener('abort',onAbort,{once:true});
          queue.push(item);
          pump();
        });
      },
      stats:()=>({active,queued:queue.length})
    };
  }
  const defaultFrameGate=createFrameGate(2);
  async function loadResult(query,{fetcher=fetch,wait=ms=>new Promise(r=>setTimeout(r,ms)),signal,bypassCache=false,cache=(fetcher===fetch?resultCache:null)}={}){
    const cacheKey=resultCacheKey(query);
    const cached=!bypassCache&&cacheKey?cache?.get(cacheKey):null;
    const etag=cached?resultCacheEtags.get(cached):null;
    for(let attempt=0;attempt<3;attempt++){
      if(signal?.aborted)throw Object.assign(Error('已取消读取'),{name:'AbortError'});
      try{
        // Revalidate every reopen on the server. A local body cache must never
        // bypass live grants or published configuration changes.
        const response=await fetcher(publicUrls.route('/natural-data/api/result?')+query,
          {signal,cache:'no-store',...(etag?{headers:{'If-None-Match':etag}}:{})});
        if(response.status===304&&cached){
          await response.text();
          cache.delete(cacheKey);cache.set(cacheKey,cached);
          return cloneResultSnapshot(cached);
        }
        if(response.ok){
          const data=await response.json();
          if(cache&&cacheKey)cacheResultEntry(cache,cacheKey,data,response.headers?.get('etag'));
          return data;
        }
        if(cache&&cacheKey&&[401,403,404].includes(response.status))cache.delete(cacheKey);
        const messages={401:'登录已过期，请刷新页面后重新打开结果。',403:'当前无法访问此结果，请检查项目的数据权限。',404:'未找到已保存的结果，请检查结果是否已移除。'};
        throw Object.assign(Error(messages[response.status]||'暂时无法读取已保存的结果，请重新加载。'),{status:response.status});
      }catch(error){
        if(signal?.aborted||error.name==='AbortError')throw error;
        const retryable=!error.status||[408,429,500,502,503,504].includes(error.status);
        if(!retryable||attempt===2)throw error.status?error:Error('连接暂时中断，请重新加载已保存的结果。');
        await wait(300*(attempt+1));
      }
    }
  }
  ViewController.loadResult=loadResult;
  ViewController.resultCache=resultCache;
  ViewController.clearResultCache=()=>{resultCache.clear();resultMutationMarks.clear();};
  ViewController.invalidateResultCache=invalidateResultRef;
  ViewController.createFrameGate=createFrameGate;
  ViewController.readFrameHeight=readFrameHeight;
  ViewController.writeFrameHeight=writeFrameHeight;

  const css=`
.nw-settings{display:flex;flex-direction:column;width:100%;color:var(--dsw-alias-label-primary)}
.nw-setting-row{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:16px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.nw-setting-copy{min-width:0;flex:1}.nw-setting-title{font-size:14px;line-height:20px;font-weight:400}
.nw-setting-description,.nw-setting-status{color:var(--dsw-alias-label-secondary);margin-top:4px;font-size:12px;line-height:18px;overflow-wrap:anywhere}
.nw-setting-row>button,.nw-setting-row>[role=switch]{flex-shrink:0}
.nw-setting-section{padding:16px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.nw-setting-details{padding:16px 0 8px 24px;display:grid;gap:16px;font-size:12px;line-height:18px}
.nw-setting-details ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.nw-setting-details code{font-size:11px;color:var(--dsw-alias-label-secondary);overflow-wrap:anywhere}
.nw-setting-status{margin:12px 0 0;min-height:18px}.nw-setting-status[data-error=true]{color:var(--dsw-alias-state-error-primary)}
.nw-settings>:last-child{border-bottom:none}
.nw-settings-tabs{padding:8px 0 16px}
.nw-source{border-bottom:.5px solid var(--dsw-alias-border-l2);padding:16px 0}
.nw-source-header{display:flex;align-items:center;gap:16px}.nw-source-header>div{flex:1;min-width:0}
.nw-source-meta{padding-left:24px;margin-top:4px}
.nw-source-body{margin:12px 0 0 24px;padding-left:16px;border-left:.5px solid var(--dsw-alias-border-l2)}
.nw-source-body .nw-setting-section{border:0}.nw-source-body .nw-setting-row{padding-top:8px}
.nw-settings-panel{display:flex;flex-direction:column;gap:16px;padding:8px 0 16px;min-width:0}.nw-settings-panel:focus{outline:none}
.nw-menu-anchor{display:inline-flex;max-width:100%;flex-shrink:0}
.nw-settings-menu{box-sizing:border-box;width:var(--nw-menu-width,300px)!important;max-width:var(--nw-menu-width,300px)!important;max-height:var(--nw-menu-height,260px)!important;overflow-y:auto!important;overflow-x:hidden!important}
.nw-settings-menu [role=menuitem]{white-space:normal;overflow-wrap:anywhere}
.nw-actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;align-items:center;min-width:0}
.nw-settings .nw-setting-row{align-items:flex-start;flex-wrap:wrap;row-gap:12px}.nw-settings .nw-setting-copy{flex:1 1 220px}.nw-settings .nw-setting-row>.nw-actions{flex:0 1 auto;max-width:100%}
.nw-settings .nw-setting-row>.nw-menu-anchor{flex-shrink:1;min-width:0;max-width:100%}.nw-settings .nw-menu-anchor>button{max-width:100%;white-space:normal;overflow-wrap:anywhere}
.nw-settings .nw-actions>button{flex-shrink:0;max-width:100%;white-space:normal;height:auto;min-height:28px}
.nw-field{display:grid;gap:8px;min-width:0;font-size:12px;line-height:18px}.nw-field>span{color:var(--dsw-alias-label-secondary)}
.nw-settings .nw-field{margin:0}.nw-settings .nw-setting-description{margin-bottom:0}
.nw-member-form{display:grid;gap:16px;padding:8px 0}.nw-member-form .nw-actions{justify-content:flex-start}
.nw-settings-tabs{max-width:100%;overflow-x:auto}.nw-settings-tabs:not(:first-child){padding-top:20px}
.nw-field input{width:100%;min-width:0;box-sizing:border-box}.nw-business-entry{display:grid;gap:10px;padding:12px 0;border-bottom:.5px solid var(--dsw-alias-border-l2)}
.nw-danger-zone{margin-top:28px;padding-top:20px;border-top:1px solid var(--dsw-alias-border-l2)}
.nw-danger-zone .nw-setting-title{color:var(--dsw-alias-state-error-primary);font-weight:500}.nw-danger-button{color:var(--dsw-alias-state-error-primary)!important;border-color:var(--dsw-alias-state-error-primary)!important}
.nw-connection-form{display:grid;gap:14px;padding:16px 0}.nw-connection-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.nw-connection-fields>.nw-field-wide{grid-column:1/-1}
.nw-connection-form .nw-field{margin:0}.nw-connection-form .nw-actions{justify-content:flex-start}.nw-connection-form input{font:inherit}
@media(max-width:600px){.nw-connection-fields{grid-template-columns:minmax(0,1fr)}}
.nw-table-options{display:grid;gap:10px;padding-top:12px}.nw-settings-panel .nw-setting-row>div:first-child{overflow-wrap:anywhere}
@media(max-width:600px){.nw-settings>.nw-setting-row{flex-wrap:wrap}.nw-settings>.nw-setting-row>.nw-actions{max-width:100%}}
.nw-data-tool{border:.5px solid var(--dsw-alias-border-l2);border-radius:12px;padding:12px;margin:6px 0;color:var(--dsw-alias-label-primary)}
/* alpha.2 widens Markdown tables past the chat column. Scope the override to
   a conversation flow containing our result slot; retain the native wrapper. */
[data-slot="conversation.chat.flow"]:has([data-natural-data-answer]) .md-table-wide{box-sizing:border-box;width:100%;max-width:100%;margin-left:0;padding-left:0;max-height:min(420px,60vh);overflow:auto}
/* A twelve-month table must stay readable without swallowing the page; the
   header stays put while the rows scroll. */
[data-slot="conversation.chat.flow"]:has([data-natural-data-answer]) .md-table-wide>table thead th{position:sticky;top:0;z-index:1;background:var(--dsw-alias-bg-base);box-shadow:inset 0 -1px var(--dsw-alias-border-l2)}
[data-natural-data-answer]{min-width:0;max-width:100%}
.nw-data-tool-header{display:flex;align-items:center;gap:8px;font-size:13px;line-height:20px}
.nw-data-tool iframe{border:0;width:100%;height:280px;margin-top:8px;display:block;color-scheme:inherit;border-radius:8px}
.nw-data-tool-stage{position:relative;margin-top:8px;border-radius:8px;overflow:hidden}
.nw-data-tool .nw-data-tool-stage iframe{margin-top:0;width:100%;height:100%;display:block;border:0;color-scheme:inherit;border-radius:8px;opacity:0;pointer-events:none}
.nw-data-tool .nw-data-tool-stage iframe.nw-frame-ready{opacity:1;pointer-events:auto;transition:opacity 120ms ease}
.nw-skeleton{position:absolute;inset:0;padding:14px;display:flex;flex-direction:column;gap:12px;background:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.04));border-radius:8px;pointer-events:none;box-sizing:border-box}
.nw-skel-line{border-radius:6px;background:linear-gradient(90deg,rgba(128,128,128,.08) 25%,rgba(128,128,128,.16) 50%,rgba(128,128,128,.08) 75%);background-size:200% 100%;animation:nw-shimmer 1.4s ease-in-out infinite}
.nw-skel-bars{flex:1;display:flex;align-items:flex-end;gap:14px;padding:12px 8px 4px}
.nw-skel-bars>span{flex:1;border-radius:6px 6px 0 0}
.nw-skel-rows{flex:1;display:grid;gap:10px;align-content:start;padding-top:4px}
.nw-skel-grid{flex:1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.nw-skel-circle{flex:1;display:flex;align-items:center;justify-content:center;gap:20px}
.nw-skel-circle-ring{width:132px;height:132px;border-radius:50%;flex-shrink:0}
.nw-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@keyframes nw-shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media(prefers-reduced-motion:reduce){.nw-data-tool .nw-data-tool-stage iframe.nw-frame-ready{transition:none}.nw-skel-line{animation:none}}
.nw-process-results{margin:6px 0;color:var(--dsw-alias-label-secondary);font-size:13px}.nw-process-list{padding-left:8px}
@media(max-width:600px){.nw-setting-row{gap:12px}.nw-setting-details{padding-left:0}}
`;

  function SettingRow({title,description,children}){return h('div',{className:'nw-setting-row'},h('div',{className:'nw-setting-copy'},h('div',{className:'nw-setting-title'},title),description&&h('div',{className:'nw-setting-description'},description)),children);}
  function Detail({title,children,initialOpen=false}){const [open,setOpen]=React.useState(initialOpen);return h('div',{className:'nw-setting-section'},h(DisclosureRow,{title,open,expandable:true,expandOnRowClick:true,icon:h(IconDataOutlineMedium,{size:16}),onToggle:()=>setOpen(v=>!v)},h('div',{className:'nw-setting-details'},children)));}
  async function settingsRequest(op,body,query){
    const owner=lifetime;
    if(op==='business_update')body={...body,response_view:'manager'};
    // Client modules may arrive before the asynchronous engine becomes ready.
    // Retry reads only; never repeat a write whose result is unknown.
    for(let attempt=0;;attempt++){
      owner.signal.throwIfAborted();
      const url=publicUrls.route('/natural-data/api/')+op+(query?'?'+new URLSearchParams(query):'');
      const r=await fetch(url,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});
      const raw=await r.text();
      if(op==='config'&&[404,503].includes(r.status)&&attempt<19){
        await new Promise((resolve,reject)=>{const abort=()=>{clearTimeout(timer);reject(new DOMException('插件已停用','AbortError'));};const timer=setTimeout(()=>{owner.signal.removeEventListener('abort',abort);resolve();},250);owner.signal.addEventListener('abort',abort,{once:true});if(owner.signal.aborted)abort();});
        continue;
      }
      let v;try{v=raw?JSON.parse(raw):null;}catch{throw Error('服务响应格式错误，请重新读取配置。');}
      if(!r.ok||v===null){const error=Error(v?.error||'数据引擎尚未就绪，请稍后重新读取配置。');error.status=r.status;throw error;}
      if(CACHE_INVALIDATE_MUTATIONS.has(op))resultCache.clear();
      return v;
    }
  }
  // Keep native menus inside the modal focus scope and every scrolling ancestor.
  function settingsMenuPlacement(node){
    const a=node.getBoundingClientRect(),bounds={top:0,bottom:innerHeight,left:0,right:innerWidth};
    for(let parent=node.parentElement;parent;parent=parent.parentElement){
      const style=getComputedStyle(parent),r=parent.getBoundingClientRect();
      if(/auto|scroll|hidden|clip/.test(style.overflowX)){bounds.left=Math.max(bounds.left,r.left);bounds.right=Math.min(bounds.right,r.right);}
      if(/auto|scroll|hidden|clip/.test(style.overflowY)){bounds.top=Math.max(bounds.top,r.top);bounds.bottom=Math.min(bounds.bottom,r.bottom);}
    }
    const below=bounds.bottom-a.bottom-8,above=a.top-bounds.top-8,side=below>=Math.min(220,above)?'bottom':'top';
    const start=bounds.right-a.left-8,end=a.right-bounds.left-8,align=start>=Math.min(300,end)?'start':'end';
    return {side,align,height:Math.max(32,Math.min(300,side==='bottom'?below:above)),width:Math.max(32,Math.min(300,align==='start'?start:end))};
  }
  function SettingsMenu({anchor,open,...props}){
    const root=React.useRef(null),[placement,setPlacement]=React.useState({side:'bottom',align:'start',height:260,width:300});
    React.useLayoutEffect(()=>{
      if(!open)return;
      const measure=()=>{if(root.current)setPlacement(settingsMenuPlacement(root.current.querySelector('button')||root.current));};
      measure();window.addEventListener('resize',measure);window.addEventListener('scroll',measure,true);
      return()=>{window.removeEventListener('resize',measure);window.removeEventListener('scroll',measure,true);};
    },[open]);
    return h('span',{ref:root,className:'nw-menu-anchor',onKeyDownCapture:e=>{if(open&&e.key==='Escape'){e.preventDefault();e.stopPropagation();props.onClose();root.current?.querySelector('button')?.focus();}},style:{'--nw-menu-height':placement.height+'px','--nw-menu-width':placement.width+'px'}},h(Menu,{...props,open,anchor,align:placement.align,side:placement.side,listClassName:'nw-settings-menu'}));
  }
  function DeleteAction({name,description,disabled,onDelete,affected=[]}){
    const [confirm,setConfirm]=React.useState(false),[detach,setDetach]=React.useState(false);
    return h('div',{className:'nw-danger-zone'},h(SettingRow,{title:'删除'+name,description},confirm?h('div',{className:'nw-actions'},h(Button,{variant:'outline',className:'nw-danger-button',size:'sm',disabled:disabled||(affected.length>0&&!detach),onClick:()=>onDelete(detach)},'确认删除'+name),h(Button,{variant:'ghost',size:'sm',disabled,onClick:()=>{setConfirm(false);setDetach(false);}},'取消删除')):h(Button,{variant:'outline',className:'nw-danger-button',size:'sm',disabled,onClick:()=>setConfirm(true)},'删除'+name)),confirm&&affected.length>0&&h(Checkbox,{label:'同时移除以下项目的数据源引用，立即撤销查询授权：'+affected.join('、'),checked:detach,disabled,onChange:setDetach}));
  }
  function ProjectSources({sources,draft,setDraft,editable,busy,onTest,onManage}){
    const connected=sources.filter(s=>draft.source_ids.includes(s.id)),available=sources.filter(s=>!draft.source_ids.includes(s.id));
    const row=s=>h(ProjectSource,{key:s.id,source:s,draft,setDraft,editable,busy,onTest,onManage});
    return h(React.Fragment,null,
      h('p',{className:'nw-setting-description'},'草稿保存并发布后生效。移除数据源会同时移除草稿中关联的查询示例和结构化指标；发布后撤销查询授权，历史结果保留。'),
      connected.map(row),
      !connected.length&&h('p',{className:'nw-setting-description'},'尚未接入数据源。'),
      editable&&available.length>0&&h(Detail,{title:'接入其他数据源（'+available.length+'）'},available.map(row)));
  }
  function ProjectSource({source:s,draft,setDraft,editable,busy,onTest,onManage}){
    const [open,setOpen]=React.useState(false),enabled=draft.source_ids.includes(s.id);
    function bind(checked){setDraft(d=>({...d,source_ids:checked?[...d.source_ids,s.id]:d.source_ids.filter(id=>id!==s.id),tables:checked?{...d.tables,[s.id]:s.tables.map(t=>t.name)}:Object.fromEntries(Object.entries(d.tables).filter(([id])=>id!==s.id)),examples:checked?d.examples:d.examples.filter(r=>r.source_id!==s.id),terms:checked?d.terms:d.terms.filter(r=>r.config?.kind!=='semantic-metric'||r.config.source_id!==s.id)}));}
    function table(name,checked){setDraft(d=>({...d,tables:{...d.tables,[s.id]:checked?[...d.tables[s.id],name]:d.tables[s.id].filter(n=>n!==name)}}));}
    return h('div',{className:'nw-source'},
      h('div',{className:'nw-source-header'},h('div',null,h(DisclosureRow,{title:s.name,open,expandable:true,expandOnRowClick:true,icon:h(IconDataOutlineMedium,{size:16}),onToggle:()=>setOpen(v=>!v)}),
        h('div',{className:'nw-setting-description nw-source-meta'},`${enabled?'已接入':'未接入'}${s.enabled===false?' · 连接已停用':''} · ${s.dialect} · ${enabled?(draft.tables[s.id]||[]).length:0}/${s.table_count} 张表${s.synthetic?' · 模拟数据':''}`)),
        h('div',{className:'nw-actions'},onManage&&h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>onManage(s.id)},'管理连接'),h(Switch,{label:'在业务项目中接入'+s.name,checked:enabled,disabled:busy||!editable,onChange:bind}))),
      open&&h('div',{className:'nw-source-body'},
        enabled&&editable&&h(SettingRow,{title:'连接检查',description:'先保存草稿，再测试连接。'},h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>onTest(s.id)},'测试连接')),
        h('div',{className:'nw-setting-description'},enabled?'仅所选表可用于该项目的查询。':'接入数据源后可以选择授权表。'),
        enabled&&h('div',{className:'nw-table-options'},s.tables.map(t=>h(Checkbox,{key:t.name,label:(t.label?t.label+' · ':'')+t.name,checked:(draft.tables[s.id]||[]).includes(t.name),disabled:busy||!editable,onChange:checked=>table(t.name,checked)})),(draft.tables[s.id]||[]).filter(name=>!s.tables.some(t=>t.name===name)).map(name=>h(Checkbox,{key:name,label:name+'（源库中已不存在，请取消授权）',checked:true,disabled:busy||!editable,onChange:checked=>table(name,checked)})))));
  }
  const {OnboardingPanel,MetricBuilder,TemplateTransfer,PackImport}=createProductSettings({React,h,Input,Button,Checkbox,SettingRow,Detail,SettingsMenu,IconChevronDownOutlineMedium,request:settingsRequest});
  const businessGroups=[['terms','业务术语与指标口径',['name','名称'],['definition','定义、单位、计算及时间规则']],['examples','查询示例',['question','业务问题'],['logical_sql','只读 SQL']],['skills','分析方法说明',['name','名称'],['description','适用范围与业务规则']],['layers','空间图层说明',['name','名称'],['description','图层来源及适用范围']]];
  function BusinessEditor({project,draft,setDraft,editable,busy,sources}){
    const [editing,setEditing]=React.useState(null);
    function change(group,id,field,value){setDraft(d=>({...d,[group]:d[group].map(row=>row.id===id?{...row,[field]:value}:row)}));}
    return h(React.Fragment,null,
      h('p',{className:'nw-setting-description'},'展开下方分类可添加、编辑和移除条目。配置归属当前业务项目。保存草稿不会改变正在生效的口径；发布后，新执行读取新版本。分析技能可绑定插件内的只读方法；指标可配置结构化计算口径。空间功能本期未启用。'),
      editable&&h(Button,{variant:'outline',size:'sm',disabled:busy||!draft.source_ids.length,onClick:()=>setEditing({})},'用表单配置指标'),
      editing&&h(MetricBuilder,{key:editing.id||'new',project,draft,setDraft,sources,disabled:busy,initial:editing.id?editing:null,onClose:()=>setEditing(null)}),
      businessGroups.map(([group,label,...fields])=>h(Detail,{key:group,title:label+'（'+draft[group].length+'）'},
        draft[group].map(row=>h('div',{key:row.id,className:'nw-business-entry'},
          fields.map(([field,title])=>editable?h('label',{key:field,className:'nw-field'},h('span',null,title),h(Input,{value:row[field]||'','aria-label':label+' '+title,disabled:busy,onChange:e=>change(group,row.id,field,e.target.value)})):h('div',{key:field},h('div',{className:'nw-setting-title'},row[field]))),
          group==='terms'&&row.config?.kind==='semantic-metric'&&editable&&h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>setEditing(row)},'用表单编辑指标'),
          ['terms','skills'].includes(group)&&editable&&h(ContractEditor,{row,group,disabled:busy,onChange:(config,config_draft)=>setDraft(d=>({...d,[group]:d[group].map(r=>{if(r.id!==row.id)return r;const next={...r};if(config===undefined)delete next.config;else next.config=config;if(config_draft===undefined)delete next.config_draft;else next.config_draft=config_draft;return next;})}))}),
          group==='examples'&&h(ExampleSource,{row,sources:sources.filter(s=>draft.source_ids.includes(s.id)),disabled:busy||!editable,onChange:id=>change(group,row.id,'source_id',id)}),
          editable&&h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:()=>setDraft(d=>({...d,[group]:d[group].filter(item=>item.id!==row.id)}))},'移除条目'))),
        !draft[group].length&&h('p',{className:'nw-setting-description'},'尚未配置'),
        editable&&h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>setDraft(d=>({...d,[group]:[...d[group],{id:crypto.randomUUID(),...Object.fromEntries(fields.map(([key])=>[key,''])),...(group==='examples'?{source_id:d.source_ids[0]||''}:{})}]}))},'添加'+label))));
  }
  function ContractEditor({row,group,onChange,disabled}){
    const [raw,setRaw]=React.useState(()=>row.config?JSON.stringify(row.config):''),[error,setError]=React.useState('');
    function edit(text){setRaw(text);try{const value=text.trim()?JSON.parse(text):undefined;if(value!==undefined&&(!value||typeof value!=='object'||Array.isArray(value)))throw Error('需要JSON对象');onChange(value,undefined);setError('');}catch{onChange(row.config,text);setError('结构化配置尚未完成，补全后才能保存。');}}
    return h(Detail,{title:group==='terms'?'高级指标配置（JSON）':'可执行分析技能'},
      h('p',{className:'nw-setting-description'},group==='terms'?'普通指标可使用上方表单。高级配置支持快照、筛选及唯一键关联；空配置仅为术语说明。保存并发布后才可执行。':'使用已安装的matrix方法、字段与参数组合成项目技能。只运行声明式只读算子。'),
      h(Input,{'aria-label':group==='terms'?'结构化指标口径 JSON':'分析技能配置 JSON',value:raw,disabled,onChange:e=>edit(e.target.value),placeholder:group==='terms'?'{"kind":"semantic-metric", ...}':'{"kind":"analysis-skill", ...}'}),
      error&&h('p',{role:'alert'},error));
  }
  function ExampleSource({row,sources,disabled,onChange}){
    const [open,setOpen]=React.useState(false);
    return h(SettingsMenu,{open,onClose:()=>setOpen(false),selectedId:row.source_id,items:sources.map(s=>({id:s.id,label:s.name})),onSelect:id=>{onChange(id);setOpen(false);},anchor:h(Button,{variant:'outline',size:'sm',disabled,onClick:()=>setOpen(v=>!v),'aria-label':'查询示例数据源'},sources.find(s=>s.id===row.source_id)?.name||'选择数据源',h(IconChevronDownOutlineMedium,{size:16}))});
  }
  function CapabilitySettings({project,editable,systemAdmin}){
    const [policy,setPolicy]=React.useState(null),[scope,setScope]=React.useState('project'),[open,setOpen]=React.useState(''),[busy,setBusy]=React.useState(false),[message,setMessage]=React.useState(''),[failed,setFailed]=React.useState(false);
    const labels={enabled:'启用',history:'仅查看历史',disabled:'禁用访问'};
    async function load(){const r=await fetch(publicUrls.route('/natural-data/api/policy?')+new URLSearchParams({project}));const v=await r.json();if(!r.ok)throw Error(v.error||'能力设置读取失败');setPolicy(v);}
    React.useEffect(()=>{load().catch(e=>{setFailed(true);setMessage(e.message);});},[project]);
    async function change(changes,skills){setOpen('');setBusy(true);setFailed(false);setMessage('');try{const v=await settingsRequest('policy_update',{project,business_project:project,scope,changes,skills,expected_revision:policy.revision});setPolicy(v);setMessage('能力设置已保存；该项目所有工作区中的会话均按新状态执行。');}catch(e){setFailed(true);setMessage(e.message);try{await load();}catch{}}finally{setBusy(false);}}
    if(!policy)return h('p',{role:failed?'alert':'status',className:'nw-setting-status'},message||'正在读取能力设置…');
    return h(React.Fragment,null,
      h(SettingRow,{title:'配置范围',description:'全局限制优先于业务项目；停止问数时取消在途数据库查询。'},h(SettingsMenu,{open:open==='scope',onClose:()=>setOpen(''),selectedId:scope,items:[{id:'project',label:'当前业务项目'},{id:'global',label:'全部业务项目',disabled:!systemAdmin}],onSelect:v=>{setScope(v);setOpen('');},anchor:h(Button,{variant:'ghost',size:'sm',disabled:busy||!editable,onClick:()=>setOpen('scope'),'aria-label':'能力配置范围'},scope==='global'?'全部业务项目':'当前业务项目',h(IconChevronDownOutlineMedium,{size:16}))})),
      policy.capabilities.map(cap=>{const configured=(scope==='global'?policy.global:policy.project)[cap.id]||cap.default||'enabled';return h(SettingRow,{key:cap.id,title:cap.title,description:`当前有效状态：${labels[cap.state]}${cap.requires.length?' · 依赖 '+cap.requires.join('、'):''}`},h(SettingsMenu,{open:open===cap.id,onClose:()=>setOpen(''),selectedId:configured,items:Object.entries(labels).map(([id,label])=>({id,label})),onSelect:state=>change({[cap.id]:state}),anchor:h(Button,{variant:'outline',size:'sm',disabled:busy||!editable,onClick:()=>setOpen(cap.id),'aria-label':cap.title+'能力状态'},labels[configured],h(IconChevronDownOutlineMedium,{size:16}))}));}),
      message&&h('p',{role:failed?'alert':'status','data-error':failed,className:'nw-setting-status'},message));
  }
  function ProjectSkills({project,editable}){
    const [catalog,setCatalog]=React.useState(null),[busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
    async function load(){const r=await fetch(publicUrls.route('/natural-data/api/project_skills?')+new URLSearchParams({business_project:project}));const v=await r.json();if(!r.ok)throw Error(v.error);setCatalog(v);}
    React.useEffect(()=>{load().catch(e=>setError(e.message));},[project]);
    async function toggle(skill,value){setBusy(true);setError('');try{await settingsRequest('policy_update',{project,business_project:project,scope:'project',changes:{},skills:{[skill.id]:value},expected_revision:catalog.revision});await load();}catch(e){setError(e.message);try{await load();}catch{}}finally{setBusy(false);}}
    return h(Detail,{title:'已安装的分析技能'},h('p',{className:'nw-setting-description'},'技能来自已安装能力插件。通用分析可复用；业务技能按当前项目的指标合约判断适用性。算法代码与版本由插件维护。'),
      (catalog?.skills||[]).map(skill=>h(SettingRow,{key:skill.id,title:skill.title,description:(skill.category||'扩展技能')+' · v'+skill.version+' · '+skill.requires+(skill.applicable?'':' · 当前项目缺少所需指标，无法启用')},h(Switch,{label:skill.title+'技能',checked:skill.enabled&&skill.applicable,disabled:busy||!editable||!skill.applicable,onChange:value=>toggle(skill,value)}))),
      error&&h('p',{role:'alert',className:'nw-setting-status','data-error':true},error));
  }
  function ProjectMetadata({project,busy,disabled,onSave}){
    const [name,setName]=React.useState(project.name),[description,setDescription]=React.useState(project.description||'');
    return h(Detail,{title:'项目基本信息'},h('label',{className:'nw-field'},h('span',null,'项目名称'),h(Input,{value:name,'aria-label':'项目名称',disabled:busy||disabled,onChange:e=>setName(e.target.value)})),h('label',{className:'nw-field'},h('span',null,'项目说明'),h(Input,{value:description,'aria-label':'项目说明',disabled:busy||disabled,onChange:e=>setDescription(e.target.value)})),h(Button,{variant:'outline',size:'sm',disabled:busy||disabled||!name.trim()||(name===project.name&&description===(project.description||'')),onClick:()=>onSave({name:name.trim(),description})},'保存项目信息'),disabled&&h('p',{className:'nw-setting-description'},'请先保存或放弃上方草稿，再修改项目信息。'));
  }
  function ProjectMembers({project,busy,disabled,onUpdate}){
    const [user,setUser]=React.useState(''),[role,setRole]=React.useState('viewer'),[open,setOpen]=React.useState(false);
    const labels={viewer:'可咨询',editor:'可编辑与发布',admin:'项目管理员'};
    return h(React.Fragment,null,
      h('p',{className:'nw-setting-description'},'使用账户系统提供的可信身份标识授权。当前宿主识别本机账户；添加标识不会创建登录账户。'),
      Object.entries(project.members||{}).map(([id,r])=>h(SettingRow,{key:id,title:id,description:labels[r]},h('div',{className:'nw-actions'},h(Button,{variant:'outline',size:'sm',disabled:busy||disabled,onClick:()=>{setUser(id);setRole(r);}},'编辑角色'),h(Button,{variant:'ghost',size:'sm',disabled:busy||disabled||(r==='admin'&&Object.values(project.members).filter(role=>role==='admin').length===1),onClick:()=>onUpdate({user:id,role:'remove'})},'移除授权')))),
      h('div',{className:'nw-member-form'},h('label',{className:'nw-field'},h('span',null,'人员身份标识'),h(Input,{value:user,disabled:busy||disabled,placeholder:'例如 local:用户名',onChange:e=>setUser(e.target.value),'aria-label':'人员身份标识'})),
      h('div',{className:'nw-actions'},h(SettingsMenu,{open,onClose:()=>setOpen(false),selectedId:role,items:Object.entries(labels).map(([id,label])=>({id,label})),onSelect:id=>{setRole(id);setOpen(false);},anchor:h(Button,{variant:'outline',size:'sm',disabled:busy||disabled,onClick:()=>setOpen(v=>!v),'aria-label':'成员角色'},labels[role],h(IconChevronDownOutlineMedium,{size:16}))}),h(Button,{variant:'outline',size:'sm',disabled:busy||disabled||!user.trim(),onClick:()=>onUpdate({user:user.trim(),role})},'保存人员授权'))));
  }
  function DatabaseConnections({preferred,onChanged,onDirtyChange}){
    const [catalog,setCatalog]=React.useState(null),[selected,setSelected]=React.useState(preferred||''),[form,setForm]=React.useState(null),[open,setOpen]=React.useState(''),[busy,setBusy]=React.useState(false),[message,setMessage]=React.useState(''),[failed,setFailed]=React.useState(false),[search,setSearch]=React.useState('');
    React.useEffect(()=>{if(preferred){setSelected(preferred);setForm(null);setMessage('');}},[preferred]);
    React.useEffect(()=>{onDirtyChange?.(Boolean(form));},[Boolean(form),onDirtyChange]);
    function adopt(value){setCatalog(value);setSelected([value.selected_source,selected].find(id=>value.connections.some(s=>s.id===id))||'');}
    async function load(){adopt(await settingsRequest('connections'));}
    React.useEffect(()=>{load().catch(e=>{setFailed(true);setMessage(e.message);});},[]);
    const source=catalog?.connections.find(s=>s.id===selected),types=catalog?.types||[];
    function edit(value){if(value)setSelected(value.id);setFailed(false);setMessage('');setForm(value?{name:value.name,description:value.description||'',dialect:value.dialect,...value.connection,password:'',clear_password:false,synthetic:Boolean(value.synthetic),confirm_target_change:false}:{isNew:true,name:'',description:'',dialect:'postgresql',host:'',port:5432,database:'',schema:'public',username:'',password:'',sslmode:'verify-full',ssl_ca:'',path:'',synthetic:false,clear_password:false,confirm_target_change:false});}
    function change(key,value){setForm(f=>({...f,[key]:value,confirm_target_change:false}));setMessage('');}
    const targetChanged=Boolean(form&&source&&['dialect',...(form.dialect==='sqlite'?['path']:['host','port','database','schema'])].some(key=>String(form[key]??'')!==String((key==='dialect'?source.dialect:source.connection[key])??'')));
    async function perform(operation,args,success){setBusy(true);setFailed(false);setMessage('');try{const value=await settingsRequest(operation,args);if(operation==='connection_update'){adopt(value);setForm(null);if(args.action==='delete')setSelected('');await onChanged();}setMessage(value.message||success);}catch(e){setFailed(true);setMessage(e.message);if(e.status===409)try{await load();}catch{}}finally{setBusy(false);}}
    function payload(){const {isNew,password_set,tls_warning,clear_password,confirm_target_change,...values}=form;values.port=Number(values.port);values.password_action=!isNew&&source?.connection.password_set&&!values.password&&!clear_password?'keep':'replace';if(clear_password)values.password='';return {source_id:isNew?undefined:source?.id,connection:values,confirm_target_change};}
    function field(key,label,options={}){return h('label',{className:'nw-field'+(options.wide?' nw-field-wide':'')},h('span',null,label),h(Input,{type:options.type||'text',value:form[key]??'','aria-label':label,disabled:busy||options.disabled,autoComplete:key==='password'?'new-password':'off',spellCheck:false,placeholder:options.placeholder,onChange:e=>change(key,e.target.value),...(options.type==='number'?{min:1,max:65535}:{})}));}
    function picker(key,label,items,onSelect){return h('div',{className:'nw-field-wide'},h(SettingRow,{title:label},h(SettingsMenu,{open:open===key,onClose:()=>setOpen(''),selectedId:form[key],items,onSelect:id=>{onSelect(id);setOpen('');},anchor:h(Button,{variant:'outline',size:'sm',disabled:busy,'aria-label':label,onClick:()=>setOpen(key)},items.find(i=>i.id===form[key])?.label||form[key],h(IconChevronDownOutlineMedium,{size:16}))})));}
    if(!catalog)return h('p',{role:failed?'alert':'status',className:'nw-setting-status'},message||'正在读取数据库连接…');
    return h('div',{className:'nw-connections'},
      !form&&!source&&h(React.Fragment,null,
        h(SettingRow,{title:'已保存数据库（'+catalog.connections.length+'）',description:'集中管理已保存的数据库。点击管理查看连接详情，编辑、测试或在详情底部删除。'},h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>edit(null)},'新增数据库连接')),
        h(Input,{'aria-label':'搜索数据库连接',placeholder:'按名称搜索已保存数据库',value:search,onChange:e=>setSearch(e.target.value)}),
        catalog.connections.filter(s=>!search||s.name.toLowerCase().includes(search.toLowerCase())).map(s=>h(SettingRow,{key:s.id,title:s.name,description:(s.enabled?'已启用':'已停用')+' · '+s.dialect+' · '+s.table_count+' 张表'+(s.synthetic?' · 测试或模拟数据':'')+' · '+(s.used_by.length?'关联 '+s.used_by.length+' 个项目':'未关联项目')},h('div',{className:'nw-actions'},h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>{setSelected(s.id);setMessage('');}},'管理'),h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:()=>edit(s)},'编辑')))),
        !catalog.connections.length&&h('p',{className:'nw-setting-description'},'还没有保存的数据库，请先新增数据库连接。'),
        catalog.connections.length>0&&!catalog.connections.some(s=>!search||s.name.toLowerCase().includes(search.toLowerCase()))&&h('p',{className:'nw-setting-description'},'没有匹配的数据库。')),
      source&&!form&&h(SettingRow,{title:source.name,description:(source.enabled?'已启用':'已停用')+' · '+source.dialect+(source.synthetic?' · 测试或模拟数据':'')},h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:()=>{setSelected('');setMessage('');}},'返回已保存数据库')),
      source&&!form&&h(React.Fragment,null,
        h(SettingRow,{title:source.dialect==='sqlite'?'数据库文件':'连接地址',description:source.dialect==='sqlite'?source.connection.path:`${source.connection.host||'未配置'}:${source.connection.port||''} / ${source.connection.database||''} · ${source.connection.username||'未配置账号'}`},h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>edit(source)},'编辑连接')),
        h(SettingRow,{title:'关联业务项目',description:source.used_by.length?source.used_by.join('、')+'。删除时可一并移除项目引用，操作将在确认后立即生效。':'尚未被业务项目接入。'}),

        h(SettingRow,{title:'连接状态',description:'停用后停止新查询并取消在途查询；保留项目配置和历史结果。'},h(Switch,{label:'启用数据库连接',checked:source.enabled,disabled:busy,onChange:enabled=>perform('connection_update',{action:'state',source_id:source.id,enabled,expected_revision:catalog.revision},enabled?'连接已启用。':'连接已停用。')})),
        source.schema_changes&&Object.values(source.schema_changes).some(v=>v.length)&&h(SettingRow,{title:'最近结构变化',description:'新增表 '+(source.schema_changes.added_tables||[]).length+' · 移除表 '+(source.schema_changes.removed_tables||[]).length+' · 字段变化表 '+(source.schema_changes.changed_tables||[]).length+'。请核对项目授权和指标口径。'}),
        h(SettingRow,{title:'连接检查与表结构',description:`已登记 ${source.table_count} 张表或视图${source.last_checked?' · 最近检查 '+new Date(source.last_checked*1000).toLocaleString():''}。刷新结构不会自动增加项目授权表。`},h('div',{className:'nw-actions'},h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>perform('connection_test',{source_id:source.id},'连接成功。')},'测试连接'),h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:()=>perform('connection_update',{action:'refresh',source_id:source.id,expected_revision:catalog.revision},'表结构已更新，请检查项目的数据范围。')},'刷新表结构'))),
        h(Detail,{title:'查看表与字段（'+source.table_count+'）'},source.tables.map(t=>h('div',{key:t.name},h('div',{className:'nw-setting-title'},t.label?t.label+' · '+t.name:t.name),h('div',{className:'nw-setting-description'},t.columns.map(c=>c.name+' '+c.type).join(' · '))))),
        h('p',{className:'nw-setting-description'},source.managed?'此连接由自然 Work 独立维护，同步原系统不会覆盖本地修改。':'此连接来自已有配置。首次保存后转为自然 Work 独立维护。')),
      form&&h('form',{className:'nw-connection-form',onSubmit:e=>{e.preventDefault();perform('connection_update',{action:'save',...payload(),expected_revision:catalog.revision},'数据库连接已保存，表结构已读取；请在业务项目中配置授权范围。');}},
        h(SettingRow,{title:form.isNew?'新增数据库连接':'编辑数据库连接',description:'切换顶部分类会保留本次未保存的输入。'},h(Button,{type:'button',variant:'ghost',size:'sm',disabled:busy,onClick:()=>{setForm(null);setMessage('');}},'取消编辑')),
        h('div',{className:'nw-connection-fields'},field('name','连接名称',{wide:true}),
          picker('dialect','数据库类型',types.map(t=>({id:t.id,label:t.label+(t.available?'':'（驱动未安装）'),disabled:!t.available})),id=>setForm(f=>({...f,dialect:id,port:types.find(t=>t.id===id)?.port||5432,schema:id==='mysql'?f.database:'public',password:'',confirm_target_change:false}))),
          form.dialect==='sqlite'?field('path','SQLite 文件路径',{wide:true,placeholder:'已有数据库的绝对路径'}):h(React.Fragment,null,
            field('host','服务器地址'),field('port','端口',{type:'number'}),field('database',form.dialect==='oracle'?'Oracle服务名':form.dialect==='dameng'?'达梦实例说明':'数据库名称'),field('schema','Schema',{placeholder:form.dialect==='mysql'?'与数据库名称一致':'public'}),field('username','用户名'),field('password','密码',{type:'password',disabled:form.clear_password,placeholder:!form.isNew&&source?.connection.password_set?'留空保留已保存密码':'输入数据库密码'}),
            !form.isNew&&source?.connection.password_set&&h(Checkbox,{label:'清空已保存密码',checked:form.clear_password,disabled:busy,onChange:value=>change('clear_password',value)}),
            ['postgresql','postgis','tbase','kingbase','mysql'].includes(form.dialect)?h(React.Fragment,null,picker('sslmode','SSL 模式',[['verify-full','校验证书和主机名（推荐）'],['verify-ca','仅校验 CA'],['require','仅加密，不校验证书'],['prefer','允许回退明文'],['disable','不使用 SSL']].filter(([id])=>form.dialect!=='mysql'||id!=='prefer').map(([id,label])=>({id,label})),id=>change('sslmode',id)),field('ssl_ca','SSL CA 证书路径（可选）',{wide:true,placeholder:'MySQL 留空使用系统 CA；PostgreSQL 使用驱动默认 CA'}),form.sslmode!=='verify-full'&&h('p',{role:'alert',className:'nw-setting-description'},'当前模式未同时强制加密、校验证书和主机名；仅在已核实的受控环境中使用。')):h('p',{className:'nw-setting-description'},'厂商原生驱动的 TLS/钱包与本机运行库需按部署说明准备并单独验证。')),
          field('description','连接说明',{wide:true})),
        h(Checkbox,{label:'此连接为测试或模拟数据',checked:form.synthetic,disabled:busy,onChange:value=>change('synthetic',value)}),
        !form.isNew&&targetChanged&&source?.used_by.length>0&&h(Checkbox,{label:'确认将关联业务项目切换到新数据库目标，并重新核对授权和口径',checked:form.confirm_target_change,disabled:busy,onChange:value=>setForm(f=>({...f,confirm_target_change:value}))}),
        h('p',{className:'nw-setting-description'},'保存连接参数时测试并读取表结构，不写入源数据库。仅修改名称与说明可离线保存，其他字段不随此操作保存。密码留空保留原密码。'),
        h('div',{className:'nw-actions'},!form.isNew&&h(Button,{type:'button',variant:'outline',size:'sm',disabled:busy||!form.name.trim(),onClick:()=>perform('connection_update',{action:'metadata',source_id:source.id,name:form.name,description:form.description,expected_revision:catalog.revision},'名称与说明已保存，连接参数未更改。')},'仅保存名称与说明'),h(Button,{type:'button',variant:'outline',size:'sm',disabled:busy,onClick:()=>perform('connection_test',payload(),'连接成功。')},'测试连接'),h(Button,{type:'submit',variant:'outline',size:'sm',disabled:busy||(!form.isNew&&targetChanged&&source?.used_by.length>0&&!form.confirm_target_change)},busy?'正在处理…':'保存并读取表结构'),h(Button,{type:'button',variant:'ghost',size:'sm',disabled:busy,onClick:()=>{setForm(null);setMessage('');}},'取消编辑'))),
      message&&h('p',{role:failed?'alert':'status','data-error':failed,className:'nw-setting-status'},message),
      !form&&h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:()=>load().catch(e=>{setFailed(true);setMessage(e.message);})},'重新读取连接列表'),
      source&&!form&&h(DeleteAction,{key:source.id+':'+catalog.revision+':'+catalog.business_revision,name:'连接',disabled:busy,affected:source.used_by,description:'删除“'+source.name+'”的连接配置及凭据，不删除源数据库文件或表。保留历史记录，结果访问仍受项目授权约束；删除后需重新添加连接。',onDelete:detach=>perform('connection_update',{action:'delete',source_id:source.id,confirm_delete:true,confirm_detach:detach,expected_business_revision:catalog.business_revision,expected_revision:catalog.revision},'数据库连接已删除，源数据库未删除，相关查询授权已撤销。')}));
  }
  const {DictionaryEditor}=createAdministration({React,h,Input,Button,SettingsMenu,SettingRow,Detail,DataSettings,request:settingsRequest,IconChevronDownOutlineMedium});
  // 0.6.3: the sidebar 配置管理 opens the management console (list + edit dialogs + publish center).
  const {Console}=createConsole({React,h,UI,openSession:id=>{if(!openNativeSession)throw Error("原生会话入口尚未就绪");openNativeSession(id);},request:settingsRequest,getJson:async(op,params)=>{const r=await fetch(publicUrls.route('/natural-data/api/'+op+'?')+new URLSearchParams(params));const v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v;},SettingsMenu,MetricBuilder,PackImport,TemplateTransfer,CapabilitySettings,DatabaseConnections,OnboardingPanel});
  function DataSettings({management=false,initialView='projects',onDirtyChange}={}){
    const [cfg,setCfg]=React.useState(null),[message,setMessage]=React.useState(''),[failed,setFailed]=React.useState(false),[busy,setBusy]=React.useState(false),[project,setProject]=React.useState(''),[menu,setMenu]=React.useState(false),[section,setSection]=React.useState('sources'),[draft,setDraft]=React.useState(null),[dirty,setDirty]=React.useState(false),[name,setName]=React.useState('');
    const sectionId=React.useId(),viewId=React.useId();
    const [connectionId,setConnectionId]=React.useState('');
    const [view,setView]=React.useState(initialView),[connectionDirty,setConnectionDirty]=React.useState(false);
    React.useEffect(()=>{onDirtyChange?.(dirty||connectionDirty);},[dirty,connectionDirty,onDirtyChange]);
    function adopt(v,selected){setCfg(v);const id=selected||project||v.default_project||v.projects[0]?.id||'';const p=v.projects.find(p=>p.id===id)||v.projects[0];setProject(p?.id||'');setDraft(p?structuredClone(p.draft||p.versions.find(v=>v.version===p.published_version)?.config):null);setDirty(false);}
    React.useEffect(()=>{let active=true;settingsRequest('config').then(v=>{if(active)adopt(v,v.default_project);}).catch(e=>{if(active&&e.name!=='AbortError'){setFailed(true);setMessage(e.message);}});return()=>{active=false;};},[]);
    function edit(fn){setDraft(d=>typeof fn==='function'?fn(d):fn);setDirty(true);setMessage('');}
    const packMessage=v=>{const r=v?.pack_report||{},i=r.imported||{},sk=r.skipped||[];return '已导入草稿：字段与取值 '+(i.mappings||0)+' 条、口径说明 '+(i.terms||0)+' 条、结构化指标 '+(i.metrics||0)+' 个、查询示例 '+(i.examples||0)+' 个'+(sk.length?'；跳过 '+sk.length+' 项（'+sk.slice(0,3).map(x=>x.name+'：'+x.reason).join('；')+(sk.length>3?'…':'')+'）':'')+((r.kept||[]).length?'；保留已改条目 '+r.kept.length+' 项':'')+'。请核对后校验发布。';};
    async function action(fn,success){setBusy(true);setFailed(false);setMessage('');try{const value=await fn();adopt(await settingsRequest('config'),value?.selected_project);setMessage(typeof success==='function'?success(value):success);}catch(e){setFailed(true);setMessage(e.message);}finally{setBusy(false);}}
    function update(kind,args={},success='项目配置已保存。'){return action(()=>settingsRequest('business_update',{action:kind,business_project:project,expected_revision:cfg.revision,...args}),success);}
    // 0.6.3: an engine that did not start is named as such, with what to do.
    if(!cfg&&failed&&/^数据引擎未启动/.test(message))return h('div',null,h(SettingRow,{title:'运行环境检查：数据引擎未启动',description:message}),h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:()=>action(()=>settingsRequest('config'),'配置已读取。')},'重新检查'));
    if(!cfg)return h('div',null,h('p',{role:failed?'alert':'status',className:'nw-setting-status'},message||'正在读取配置…'),failed&&h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:()=>action(()=>settingsRequest('config'),'配置已读取。')},'重新读取配置'));
    const current=cfg.projects.find(p=>p.id===project),editable=['admin','editor'].includes(current?.role),admin=current?.role==='admin';
    function choose(id){if(dirty){setFailed(true);setMessage('请先保存或放弃修改，再切换项目。');return;}const p=cfg.projects.find(p=>p.id===id);setProject(id);setDraft(structuredClone(p.draft||p.versions.find(v=>v.version===p.published_version)?.config));setDirty(false);setMenu(false);setMessage('');}
    return h('div',{className:'nw-settings'},
      !management&&openAdministration&&h(SettingRow,{title:'配置管理后台',description:'在完整管理页面维护数据库和业务口径。'},h(Button,{variant:'outline',size:'sm',disabled:busy||dirty,onClick:()=>openAdministration?.()},'打开配置管理后台')),
      !management&&cfg.system_admin&&h('div',{className:'nw-settings-tabs'},h(SegmentedControl,{id:viewId,value:view,label:'数据源与问数管理',options:[{value:'projects',label:'业务项目'},{value:'connections',label:'数据库'}],onChange:setView})),
      cfg.system_admin&&h('div',{hidden:view!=='connections',role:'tabpanel',id:viewId+'-connections-panel','aria-labelledby':management?undefined:viewId+'-connections'},h(DatabaseConnections,{preferred:connectionId,onDirtyChange:setConnectionDirty,onChanged:async()=>{const value=await settingsRequest('config');if(dirty){setFailed(true);setMessage('数据库配置已更新；项目未保存草稿仍保留，请重新核对数据范围。');}else adopt(value);}})),
      view==='projects'&&h(OnboardingPanel,{cfg,current,busy,dirty,onNavigate:target=>{if(target==='connections'&&cfg.system_admin)setView('connections');else{setView('projects');setSection(target==='connections'?'sources':target);}}}),
      h('div',{hidden:view!=='projects',role:'tabpanel',id:viewId+'-projects-panel','aria-labelledby':cfg.system_admin?viewId+'-projects':undefined},
      h(SettingRow,{title:'业务项目',description:'配置可查询的数据库、表和业务口径。新对话使用工作区已关联的项目，未关联时使用默认项目。'},h(SettingsMenu,{open:menu,onClose:()=>setMenu(false),selectedId:project,items:cfg.projects.map(p=>({id:p.id,label:p.name})),onSelect:choose,anchor:h(Button,{variant:'ghost',size:'sm',disabled:busy||dirty||!cfg.projects.length,'aria-label':'问数业务项目','aria-haspopup':'menu','aria-expanded':menu,onClick:()=>setMenu(v=>!v)},current?.name||'暂无授权项目',h(IconChevronDownOutlineMedium,{size:16}))})),
      message&&h('p',{role:failed?'alert':'status','data-error':failed,className:'nw-setting-status'},message),
      current&&draft&&h(React.Fragment,null,
        h(SettingRow,{title:current.published_version?'生效版本 v'+current.published_version:'尚未发布',description:(dirty?'有未保存的草稿，请先保存后发布或切换项目。':editable?'展开业务口径可直接编辑；修改后保存草稿，再校验发布。':'你可以咨询和查看已发布配置。')+' 当前身份：'+cfg.principal},h('div',{className:'nw-actions'},
          h(Button,{variant:'outline',size:'sm',disabled:busy||dirty||!current.published_version||cfg.default_project===project,onClick:()=>update('default',{},'已设为新会话的默认业务项目。')},cfg.default_project===project?'新会话默认项目':'设为默认项目'),
          editable&&h(Button,{variant:'outline',size:'sm',disabled:busy||!dirty,onClick:()=>update('draft',{draft},'草稿已保存，生效版本未改变。')},'保存草稿'),
          editable&&h(Button,{variant:'outline',size:'sm',disabled:busy,onClick:async()=>{setBusy(true);setFailed(false);try{const v=await settingsRequest('admin_check',{business_project:project,expected_revision:cfg.revision,draft});setFailed(!v.valid);setMessage(v.valid?v.message:v.errors.map(x=>x.message).join('；'));}catch(e){setFailed(true);setMessage(e.message);}finally{setBusy(false);}}},'校验当前配置'),
          editable&&h(Button,{variant:'outline',size:'sm',disabled:busy||dirty,onClick:()=>update('publish',{},'项目已校验并发布。')},'校验并发布'),
          dirty&&h(Button,{variant:'ghost',size:'sm',disabled:busy,onClick:()=>action(()=>settingsRequest('config'),'已放弃修改并重新读取项目配置。')},'放弃修改'))),
        h('div',{className:'nw-settings-tabs'},h(SegmentedControl,{id:sectionId,value:section,label:'问数设置分类',options:[{value:'sources',label:'数据范围'},{value:'mappings',label:'业务映射'},{value:'business',label:'业务口径'},{value:'capabilities',label:'能力'},...(admin?[{value:'members',label:'成员'}]:[]),{value:'versions',label:'发布记录'}],onChange:setSection})),
        h('div',{className:'nw-settings-panel',role:'tabpanel',id:sectionId+'-'+section+'-panel','aria-labelledby':sectionId+'-'+section},
          section==='sources'?h(ProjectSources,{sources:cfg.sources,draft,setDraft:edit,editable:admin,busy,onManage:cfg.system_admin?sid=>{setConnectionId(sid);setView('connections');}:undefined,onTest:sid=>dirty?(setFailed(true),setMessage('请先保存草稿。')):action(()=>settingsRequest('test',{business_project:project,source_id:sid}),value=>value.message||'连接测试完成。')}):
          section==='mappings'?h(DictionaryEditor,{draft,setDraft:edit,sources:cfg.sources,editable,busy}):
          section==='business'?h(React.Fragment,null,h(BusinessEditor,{project,draft,setDraft:edit,editable,busy,sources:cfg.sources}),h(ProjectSkills,{key:project,project,editable:admin}),editable&&h(PackImport,{key:project+':packs',project:current,busy,dirty,onImport:(pack,source_id)=>update('import_pack',{pack,source_id},packMessage)}),editable&&h(TemplateTransfer,{key:project+':template',project:current,sources:cfg.sources,busy,dirty,onImport:(template,source_bindings)=>update('import_template',{template,source_bindings},'模板已校验并导入草稿，请核对后校验发布。')})):
          section==='capabilities'?h(CapabilitySettings,{key:project,project,editable:admin,systemAdmin:cfg.system_admin}):
          section==='members'&&admin?h(ProjectMembers,{project:current,busy,disabled:dirty,onUpdate:args=>update('member',args,'人员授权已保存。')}):
          section==='versions'?h(React.Fragment,null,h('p',{className:'nw-setting-description'},'只保留最近 5 个发布版本，更早的会自动清理。回滚会创建一个新版本，已有结果保留原始版本记录。'),[...current.versions].reverse().map(v=>h(SettingRow,{key:v.version,title:'v'+v.version+(v.version===current.published_version?' · 当前生效':''),description:v.note+' · '+new Date(v.at*1000).toLocaleString()},editable&&h(Button,{variant:'outline',size:'sm',title:!admin&&['source_ids','tables'].some(field=>JSON.stringify(v.config[field])!==JSON.stringify(current.draft[field]))?'该版本涉及数据范围变更，需要项目管理员恢复。':undefined,disabled:busy||dirty||v.version===current.published_version||(!admin&&['source_ids','tables'].some(field=>JSON.stringify(v.config[field])!==JSON.stringify(current.draft[field]))),onClick:()=>update('rollback',{version:v.version},'已恢复历史配置并发布新版本。')},'恢复此版本')))):null)),

      admin&&h(ProjectMetadata,{key:project+':'+current.name,project:current,busy,disabled:dirty,onSave:args=>update('metadata',args,'项目信息已保存。')}),
      h(Button,{variant:'ghost',size:'sm',disabled:busy||dirty,onClick:()=>action(()=>settingsRequest('config'),'项目配置已重新读取。')},'重新读取项目配置'),
      (cfg.archived_projects||[]).length>0&&h(Detail,{title:'已删除项目（'+cfg.archived_projects.length+'）'},cfg.archived_projects.map(p=>h(SettingRow,{key:p.id,title:p.name,description:'恢复后为未发布草稿，不自动恢复默认项目或查询权限。'},h(Button,{variant:'outline',size:'sm',disabled:busy||dirty,onClick:()=>update('restore',{business_project:p.id},'项目已恢复为草稿，请核对数据范围并发布。')},'恢复为草稿')))),
      cfg.system_admin&&h(Detail,{title:'项目与来源管理',initialOpen:!cfg.projects.length},
        h('label',{className:'nw-field'},h('span',null,'新业务项目名称'),h(Input,{value:name,disabled:busy||dirty,'aria-label':'新业务项目名称',onChange:e=>setName(e.target.value)})),
        h(Button,{variant:'outline',size:'sm',disabled:busy||dirty||!name.trim(),onClick:()=>update('create',{name:name.trim()},'业务项目已创建，请配置数据范围并发布。')},'创建业务项目'),
        cfg.legacy_sync_available!==false&&h(SettingRow,{title:'同步来源配置',description:'更新原系统仍管理的来源目录；保留本地连接修改及所有项目授权和口径。'},h(Button,{variant:'outline',size:'sm',disabled:busy||dirty,onClick:()=>action(()=>settingsRequest('sync',{}),'来源已同步，本地连接和项目范围保持不变。')},'同步智能问数'))),
admin&&h(DeleteAction,{key:project,name:'项目',disabled:busy||dirty,description:'删除“'+current.name+'”后停止新查询、清除默认选择并释放连接引用。保留历史记录并撤销结果访问，可从已删除项目恢复为草稿；恢复后需重新校验发布。',onDelete:()=>update('delete',{confirm_delete:true},'项目已删除，历史结果保留。')})));
  }
  const chartNames={table:'表格',kpi:'指标卡',bar:'条形图',column:'柱状图','grouped-bar':'分组柱状图','stacked-bar':'堆叠柱状图','percent-stacked-bar':'百分比堆叠图',line:'折线图',area:'面积图',pie:'饼图',donut:'环形图',scatter:'散点图',bubble:'气泡图',heatmap:'矩阵热力图',treemap:'树图',histogram:'直方图',radar:'雷达图',funnel:'漏斗图',gauge:'仪表盘',waterfall:'瀑布图',boxplot:'箱线图','alert-table':'预警表','pivot-table':'多维透视表'};
  const aggregationNames={sum:'合计',count:'计数',distinct:'去重计数',mean:'算术平均',min:'最小值',max:'最大值',weighted_mean:'加权平均'};
  function ReportEditor({meta,query,onSaved,onClose}){
    const defaultCell={id:crypto.randomUUID(),result_ref:meta.result_ref,title:meta.title,row_dimensions:(meta.columns||[]).filter(c=>!meta.rows?.some(r=>typeof r[c]==='number')).slice(0,2),column_dimensions:[],measures:[{column:meta.columns?.includes('value')?'value':meta.columns?.find(c=>meta.rows?.some(r=>typeof r[c]==='number'))||'',aggregation:meta.metric_contract?.ratio?'mean':'sum',weight_column:null}],filters:[],chart_type:'pivot-table',sort_by:null,descending:false,row_limit:500};
    const [spec,setSpec]=React.useState(()=>structuredClone(meta.report?.spec||{title:meta.title+' · 多维报表',columns:2,cells:[defaultCell]})),[busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),[available,setAvailable]=React.useState([]),[open,setOpen]=React.useState('');
    const project=new URLSearchParams(query).get('project');
    // 0.6.3: a failed results read is shown instead of an empty list.
    React.useEffect(()=>{fetch(publicUrls.route('/natural-data/api/results?')+new URLSearchParams({project})).then(async r=>{if(!r.ok){const v=await r.json().catch(()=>null);throw Error(v?.error||('HTTP '+r.status));}return r.json();}).then(setAvailable).catch(e=>{console.error('智能问数：读取结果列表失败',e);setError('读取可用结果失败：'+e.message);});},[project]);
    function cell(id,patch){setSpec(s=>({...s,cells:s.cells.map(c=>c.id===id?{...c,...patch}:c)}));}
    function choice(id,value,items,change){const label=v=>chartNames[v]||aggregationNames[v]||v;return h(SettingsMenu,{open:open===id,onClose:()=>setOpen(''),selectedId:value,items:items.map(v=>({id:v,label:label(v)})),onSelect:v=>{setOpen('');change(v);},anchor:h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:()=>setOpen(id)},label(value),h(IconChevronDownOutlineMedium,{size:16}))});}
    async function save(){setBusy(true);setError('');try{const body={project,spec,...(meta.report?{result_ref:meta.result_ref,expected_revision:meta.report.report_revision}:{attach_to_result_ref:meta.result_ref,expected_attachment_revision:meta.report_attachment_revision||0})};const r=await fetch(publicUrls.route('/natural-data/api/')+(meta.report?'edit_report':'make_report'),{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error||'报表保存失败');onSaved({...v,view_url:publicUrls.route('/natural-data/view?')+new URLSearchParams({project,result_ref:v.result_ref})});}catch(e){setError(e.message);}finally{setBusy(false);}}
    return h(Detail,{title:'编辑多维报表',initialOpen:true},
      h('label',{className:'nw-field'},h('span',null,'报表标题'),h(Input,{value:spec.title,disabled:busy,'aria-label':'报表标题',onChange:e=>setSpec(s=>({...s,title:e.target.value}))})),
      h(SettingRow,{title:'每行单元数'},choice('layout',String(spec.columns),['1','2','3','4'],v=>setSpec(s=>({...s,columns:Number(v)})))),
      spec.cells.map(c=>h(Detail,{key:c.id,title:c.title},
        h('label',{className:'nw-field'},h('span',null,'单元标题'),h(Input,{value:c.title,disabled:busy,'aria-label':'单元标题',onChange:e=>cell(c.id,{title:e.target.value})})),
        h(SettingRow,{title:'数据结果'},h(SettingsMenu,{open:open===c.id+'source',onClose:()=>setOpen(''),selectedId:c.result_ref,items:available.filter(r=>r.status==='succeeded').map(r=>({id:r.result_ref,label:r.title})),onSelect:v=>{cell(c.id,{result_ref:v});setOpen('');},anchor:h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:()=>setOpen(c.id+'source')},available.find(r=>r.result_ref===c.result_ref)?.title||'当前查询结果')})),
        [['row_dimensions','行维度'],['column_dimensions','列维度']].map(([key,label])=>h('label',{key,className:'nw-field'},h('span',null,label+'（逗号分隔字段）'),h(Input,{value:c[key].join(','),disabled:busy,'aria-label':label,onChange:e=>cell(c.id,{[key]:e.target.value.split(',').map(x=>x.trim()).filter(Boolean)})}))),
        c.measures.map((m,j)=>h('div',{key:j,className:'nw-actions'},h(Input,{value:m.column,disabled:busy,'aria-label':'度量字段',onChange:e=>cell(c.id,{measures:c.measures.map((v,k)=>k===j?{...v,column:e.target.value}:v)})}),choice(c.id+'agg'+j,m.aggregation,['sum','count','distinct','mean','min','max','weighted_mean'],v=>cell(c.id,{measures:c.measures.map((x,k)=>k===j?{...x,aggregation:v}:x)})),m.aggregation==='weighted_mean'&&h(Input,{value:m.weight_column||'',disabled:busy,'aria-label':'权重字段',onChange:e=>cell(c.id,{measures:c.measures.map((x,k)=>k===j?{...x,weight_column:e.target.value}:x)})}),h(Button,{size:'sm',variant:'ghost',disabled:busy||c.measures.length===1,onClick:()=>cell(c.id,{measures:c.measures.filter((x,k)=>k!==j)})},'移除度量'))),
        h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:()=>cell(c.id,{measures:[...c.measures,{column:'value',aggregation:'mean',weight_column:null}]})},'添加度量'),
        h(SettingRow,{title:'展示图形'},choice(c.id+'chart',c.chart_type,['pivot-table','table','column','bar','line','pie','heatmap'],v=>cell(c.id,{chart_type:v}))),
        c.filters.map((f,j)=>h('div',{key:j,className:'nw-actions'},h(Input,{value:f.dimension,disabled:busy,'aria-label':'筛选字段',onChange:e=>cell(c.id,{filters:c.filters.map((x,k)=>k===j?{...x,dimension:e.target.value}:x)})}),choice(c.id+'filter'+j,f.op,['eq','ne','gt','gte','lt','lte','in'],v=>cell(c.id,{filters:c.filters.map((x,k)=>k===j?{...x,op:v}:x)})),h(Input,{value:f.values.join(','),disabled:busy,'aria-label':'筛选值',onChange:e=>cell(c.id,{filters:c.filters.map((x,k)=>k===j?{...x,values:e.target.value.split(',').map(v=>v.trim())}:x)})}),h(Button,{size:'sm',variant:'ghost',disabled:busy,onClick:()=>cell(c.id,{filters:c.filters.filter((x,k)=>k!==j)})},'移除筛选'))),
        h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:()=>cell(c.id,{filters:[...c.filters,{dimension:'period',op:'eq',values:['']}]})},'添加筛选'),
        h('label',{className:'nw-field'},h('span',null,'排序字段'),h(Input,{value:c.sort_by||'',disabled:busy,'aria-label':'排序字段',onChange:e=>cell(c.id,{sort_by:e.target.value||null})})),
        h(Checkbox,{label:'降序',checked:c.descending,disabled:busy,onChange:v=>cell(c.id,{descending:v})}),
        h(Button,{size:'sm',variant:'ghost',disabled:busy||spec.cells.length===1,onClick:()=>setSpec(s=>({...s,cells:s.cells.filter(x=>x.id!==c.id)}))},'移除单元'))),
      h('div',{className:'nw-actions'},h(Button,{size:'sm',variant:'outline',disabled:busy||spec.cells.length>=12,onClick:()=>setSpec(s=>({...s,cells:[...s.cells,{...defaultCell,id:crypto.randomUUID()}]}))},'添加报表单元'),h(Button,{size:'sm',disabled:busy,onClick:save},busy?'正在保存…':'保存报表'),h(Button,{size:'sm',variant:'ghost',disabled:busy,onClick:onClose},'取消')),
      error&&h('p',{role:'alert'},error));
  }
  function useInView(ref,rootMargin='400px 0px',settleMs=60){
    const [visible,setVisible]=React.useState(()=>typeof IntersectionObserver!=='function');
    React.useEffect(()=>{
      if(visible||typeof IntersectionObserver!=='function')return;
      const node=ref.current;if(!node)return;
      let timer=null;
      const clear=()=>{if(timer!==null){clearTimeout(timer);timer=null;}};
      const observer=new IntersectionObserver(entries=>{
        if(entries.some(e=>e.isIntersecting)){
          if(timer===null)timer=setTimeout(()=>{timer=null;setVisible(true);observer.disconnect();},settleMs);
        }else clear();
      },{rootMargin});
      observer.observe(node);
      return()=>{clear();observer.disconnect();};
    },[visible]);
    return visible;
  }
  function skeletonShape(value,meta,kind){
    if(meta?.report||value?.report)return 'report';
    const type=kind||meta?.chart?.type||value?.visualization?.actual||'chart';
    if(type==='table'||type==='pivot-table')return 'table';
    if(type==='number'||type==='kpi')return 'kpi';
    if(['pie','donut','gauge','radar'].includes(type))return 'pie';
    return 'chart';
  }
  function renderSkeleton(shape){
    if(shape==='report')return h('div',{className:'nw-skeleton','aria-hidden':'true'},
      h('div',{className:'nw-skel-line',style:{width:'38%',height:16}}),
      h('div',{className:'nw-skel-grid'},h('div',{className:'nw-skel-line',style:{height:'100%'}}),h('div',{className:'nw-skel-line',style:{height:'100%'}})));
    if(shape==='table')return h('div',{className:'nw-skeleton','aria-hidden':'true'},
      h('div',{className:'nw-skel-line',style:{width:'100%',height:28}}),
      h('div',{className:'nw-skel-rows'},[0,1,2,3].map(i=>h('div',{key:i,className:'nw-skel-line',style:{width:(94-i*8)+'%',height:20}}))));
    if(shape==='kpi')return h('div',{className:'nw-skeleton','aria-hidden':'true'},
      h('div',{className:'nw-skel-grid'},[0,1,2,3].map(i=>h('div',{key:i,className:'nw-skel-line',style:{height:88}}))));
    if(shape==='pie')return h('div',{className:'nw-skeleton','aria-hidden':'true'},
      h('div',{className:'nw-skel-circle'},h('div',{className:'nw-skel-line nw-skel-circle-ring'}),
        h('div',{style:{flex:1,display:'grid',gap:10}},[0,1,2].map(i=>h('div',{key:i,className:'nw-skel-line',style:{width:(85-i*14)+'%',height:14}})))));
    return h('div',{className:'nw-skeleton','aria-hidden':'true'},
      h('div',{className:'nw-skel-line',style:{width:'36%',height:16}}),
      h('div',{className:'nw-skel-bars'},[42,78,56,90,64].map((pct,i)=>h('span',{key:i,className:'nw-skel-line',style:{height:pct+'%'}}))));
  }
  function ToolCard(props){
    const result=props.phase==='result' ? props.block : null;
    let value; try { const content=result?.content || result?.result?.content; value=JSON.parse(content?.find(x=>x.type==='text')?.text || '{}'); } catch {}
    const [meta,setMeta]=React.useState(null),[tab,setTab]=React.useState('chart'),[menu,setMenu]=React.useState(false),[busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),[revision,setRevision]=React.useState(0);
    const [editing,setEditing]=React.useState(false),[replacement,setReplacement]=React.useState(null),[loading,setLoading]=React.useState(false);
    const [frameAllowed,setFrameAllowed]=React.useState(false),[frameReady,setFrameReady]=React.useState(false);
    const [renderedChart,setRenderedChart]=React.useState(null),[chartIntent,setChartIntent]=React.useState(null);
    const cardRef=React.useRef(null),frame=React.useRef(null),latest=React.useRef(null),sent=React.useRef(null);
    const controller=React.useRef(null),restoreScroll=React.useRef(null),viewChange=React.useRef(null),gateRelease=React.useRef(null);
    const switchIntent=React.useRef(null);
    const visible=useInView(cardRef,'400px 0px',60);
    const controlId=React.useId();
    const forecastIds=[...(props.forecastClaimIds||[])].sort().join(',');
    const frameResult=React.useMemo(()=>meta&&{...meta,forecast_claim_ids:forecastIds?forecastIds.split(','):[]},[meta,forecastIds]);
    const view=publicUrls.result(value?.view_url);
    const query=view?new URL(view,location.origin).searchParams.toString():'';
    const [frameHeight,setFrameHeight]=React.useState(()=>readFrameHeight(query));
    const mutationSig=[value?.execution?.id||'',value?.view_revision??'',value?.analysis_ref||'',value?.visualization?.actual||'',value?.report?'1':'',forecastIds].join('|');
    const prevLoadState=React.useRef({view,mutationSig,revision});
    const releaseGate=()=>{if(gateRelease.current){const fn=gateRelease.current;gateRelease.current=null;fn();}};
    React.useEffect(()=>{
      setFrameReady(false);setRenderedChart(null);setChartIntent(null);setFrameAllowed(false);setFrameHeight(readFrameHeight(query));
      return()=>{switchIntent.current=null;};
    },[view,query]);
    const captureScroll=()=>{const positions=[];for(let node=frame.current?.parentElement;node;node=node.parentElement)if(node.scrollHeight>node.clientHeight)positions.push([node,node.scrollTop,node.scrollLeft]);return()=>{for(const [node,top,left]of positions){node.scrollTop=top;node.scrollLeft=left;}};};
    const io={
      accept:(v,patch)=>{viewChange.current=patch?{result:v,patch}:null;if(patch&&v&&query)cacheResultEntry(resultCache,resultCacheKey(query),v);setMeta(v);},
      busy:setBusy,error:setError,load:()=>loadResult(query,{bypassCache:true}),
      update:async(kind,expected_revision)=>{
        const response=await fetch(publicUrls.route('/natural-data/api/visualize'),{
          method:'POST',headers:{'content-type':'application/json'},
          body:JSON.stringify({...Object.fromEntries(new URLSearchParams(query)),kind,expected_revision}),
        });
        let payload;
        try {payload=await response.json();}catch(error){if(error.name==='AbortError')throw error;}
        if(!response.ok)throw Object.assign(Error(payload?.error||'切换图表失败'),{status:response.status});
        return payload?.view_patch;
      },
    };
    if(!controller.current)controller.current=new ViewController(io);controller.current.io=io;
    React.useEffect(()=>()=>{releaseGate();controller.current?.dispose();},[]);
    React.useEffect(()=>{const restore=restoreScroll.current;if(!restore)return;restore();const id=requestAnimationFrame(()=>{restore();if(restoreScroll.current===restore)restoreScroll.current=null;});return()=>cancelAnimationFrame(id);},[meta,tab,frameHeight]);
    React.useEffect(()=>{
      if(!visible||!view)return;
      const prev=prevLoadState.current;
      const sameViewMutated=prev.view===view&&prev.mutationSig!==mutationSig;
      const retried=revision!==prev.revision;
      prevLoadState.current={view,mutationSig,revision};
      const abort=new AbortController();let live=true;
      setLoading(true);setError('');
      loadResult(query,{signal:abort.signal,bypassCache:sameViewMutated||retried}).then(v=>{
        if(!live)return;setError('');
        if(v.report_target){releaseGate();setReplacement({status:'succeeded',result_ref:v.report_target,view_url:publicUrls.route('/natural-data/view?')+new URLSearchParams({project:new URLSearchParams(query).get('project'),result_ref:v.report_target})});}
        else controller.current.accept(v);
      }).catch(e=>{if(live&&e.name!=='AbortError'){releaseGate();setError(e.message);}}).finally(()=>{if(live)setLoading(false);});
      return()=>{live=false;abort.abort();};
    },[visible,view,revision,mutationSig]);
    React.useEffect(()=>{
      if(!visible||!view||replacement||(error&&!meta))return;
      const abort=new AbortController();let live=true,safetyTimer=null;
      defaultFrameGate.acquire(abort.signal).then(release=>{
        if(!live){release();return;}
        gateRelease.current=release;
        setFrameAllowed(true);
        safetyTimer=setTimeout(()=>releaseGate(),8000);
      }).catch(e=>{if(e?.name!=='AbortError')releaseGate();});
      return()=>{live=false;if(safetyTimer!==null)clearTimeout(safetyTimer);abort.abort();releaseGate();};
    },[visible,view,replacement,Boolean(error&&!meta),revision]);
    latest.current={tab,result:frameResult,meta,forecastIds,query};
    const send=(force=false)=>{
      const target=frame.current?.contentWindow, next=latest.current;
      if(!target||!next?.result)return;
      const change=viewChange.current;
      const previous=!force&&sent.current?.target===target?sent.current.state:null;
      const sameClaims=previous?.forecastIds===next.forecastIds;
      const message=resultFrameMessage(previous,next,sameClaims&&change?.result===next.meta?change.patch.base_view_revision:undefined);
      if(message)target.postMessage(message,location.origin);
      sent.current={target,state:next};
    };
    React.useEffect(()=>{send();},[frameResult,tab]);
    React.useEffect(()=>{
      const receive=event=>{
        if(event.origin!==location.origin||event.source!==frame.current?.contentWindow)return;
        if(event.data?.type==='natural-data:ready')send(true);
        const current=latest.current;
        const message=event.data;
        const height=resultFrameHeight(message);
        if(height!==null&&current?.meta&&message.result_ref===current.meta.result_ref
          &&message.view_revision===(current.meta.view_revision??0)&&message.tab===current.tab){
          setFrameHeight(height);
          if(message.rendered!==true)return;
          if(message.tab==='chart'){
            setRenderedChart(message.result_ref+':'+message.view_revision);
            writeFrameHeight(current.query,height);
          }
          setFrameReady(true);releaseGate();
        }
      };
      window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
    },[view]);
    async function choose(kind){
      setMenu(false);
      const previous=switchIntent.current;
      if(kind===(previous?.kind??controller.current.current?.chart?.type))return;
      const intent={kind,view};
      switchIntent.current=intent;
      restoreScroll.current=captureScroll();
      setTab('chart');
      setChartIntent(intent);
      if(previous?.view===view)return;
      // Serialise saves and retain the last choice while a save is in flight.
      for(let next=intent;next?.view===view;next=switchIntent.current){
        if(controller.current.current?.chart?.type!==next.kind)await controller.current.select(next.kind);
        if(switchIntent.current===next){
          switchIntent.current=null;setChartIntent(null);
          break;
        }
      }
    }

    if(replacement)return h(ToolCard,{key:replacement.result_ref,phase:'result',block:{content:[{type:'text',text:JSON.stringify(replacement)}]}});
    const actual=meta?.chart?.type||'table',choices=[...new Set([actual,...meta?.chart?.allowedAlternatives||[],'table'])];
    const pendingKind=chartIntent?.view===view?chartIntent.kind:null;
    const selectedKind=pendingKind||actual;
    const chartIsReady=meta&&renderedChart===meta.result_ref+':'+(meta.view_revision??0);
    const showSkeleton=!frameReady||(tab==='chart'&&(Boolean(pendingKind)||!chartIsReady));
    const rawTitle=meta?.title||value?.title;
    const label=publicMetricLabel(rawTitle,meta);
    const title=resultDisplayTitle(label===rawTitle?publicMetricLabel(rawTitle,value):label,
      meta?.source_provenance||value?.source_provenance)||'问数结果';
    const shape=skeletonShape(value,meta,tab==='chart'?selectedKind:undefined);
    const showStage=view&&!(error&&!meta);
    return h('div',{ref:cardRef,className:'nw-data-tool'},
      h('div',{className:'nw-data-tool-header'},h(IconDataOutlineMedium,{size:16}),h('span',null,title)),
      meta&&h('div',{className:'nw-setting-description'},`${meta.row_count||0} 行${meta.truncated?' · 已截断，仅展示当前返回部分':''}`),
      meta&&h('div',{style:{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',marginTop:12}},
        h(SegmentedControl,{id:controlId,value:tab,label:'问数结果展示',options:[{value:'chart',label:'图表'},{value:'data',label:'数据'},{value:'receipt',label:'查询依据'}],onChange:next=>{restoreScroll.current=captureScroll();setTab(next);}}),
        !meta.report&&h(Menu,{open:menu,onClose:()=>setMenu(false),selectedId:selectedKind,items:choices.map(id=>({id,label:(chartNames[id]||id)+(id!==selectedKind&&id===meta.chart?.recommendedType?'（推荐）':'')})),onSelect:choose,anchor:h(Button,{size:'sm','aria-label':'图表类型','aria-haspopup':'menu','aria-expanded':menu,onClick:()=>setMenu(v=>!v)},chartNames[selectedKind]||selectedKind,h(IconChevronDownOutlineMedium,{size:16}))})),
      loading&&!meta&&h('span',{role:'status',className:'nw-sr-only'},'正在读取已保存的结果…'),
      meta&&tab==='chart'&&showSkeleton&&h('span',{role:'status',className:'nw-sr-only'},'正在加载'+(chartNames[selectedKind]||'图表')+'…'),
      error&&h('div',null,h('p',{role:'alert',className:'nw-setting-status','data-error':true},error),h(Button,{size:'sm',variant:'outline',disabled:loading||busy,onClick:()=>setRevision(v=>v+1)},'重新加载')),
      showStage&&h('div',{role:'tabpanel',id:controlId+'-'+tab+'-panel','aria-labelledby':controlId+'-'+tab,'aria-busy':showSkeleton||undefined,className:'nw-data-tool-stage',style:{height:frameHeight}},
        showSkeleton&&renderSkeleton(shape),
        visible&&frameAllowed&&h('iframe',{ref:frame,className:showSkeleton?'':'nw-frame-ready',tabIndex:showSkeleton?-1:undefined,'aria-hidden':showSkeleton||undefined,src:view+'&embedded=1',onLoad:()=>send(),title})),
      !view&&h(Button,{variant:'ghost',size:'sm',onClick:props.inspect},'查看详情'));
  }
  // Harness owns turn boundaries and durable replay; only successful data tool
  // results become answer artifacts. No extraction from model prose or global cache.
  const resultDefinition=resultEvents(publicUrls,{
    onMutation:(v,_name,seq)=>{
      const ref=v?.result_ref;
      if(!ref)return;
      const stamp=`${seq||''}:${v.view_revision??''}:${v.analysis_ref||''}:${v.report?1:0}`;
      if(resultMutationMarks.get(ref)===stamp)return;
      resultMutationMarks.set(ref,stamp);
      invalidateResultRef(ref);
    }
  });
  function ContinueAnswer({resume}){
    const [busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
    return h('div',{className:'nw-setting-status'},h('p',null,'已生成的查询结果已保留；后续文字整理或分析未完成，不影响下方已成功的结果。可基于已有结果继续。'),h(Button,{size:'sm',variant:'outline',disabled:busy,onClick:async()=>{setBusy(true);setError('');try{await resume();}catch{setError('未能继续，请稍后重试。');}finally{setBusy(false);}}},busy?'正在继续…':'继续完成回答'),error&&h('p',{role:'alert'},error));
  }
  function AnswerQuality({sessionId,turn,seq,hasCards}){
    const [quality,setQuality]=React.useState(null);
    React.useEffect(()=>{
      if(!sessionId)return;
      const controller=new AbortController();
      setQuality(null);
      fetch(publicUrls.route('/natural-data/api/answer-quality?')+new URLSearchParams({session:sessionId,turn:String(turn)}),{signal:controller.signal})
        .then(response=>response.ok?response.json():null).then(value=>{if(!controller.signal.aborted)setQuality(value);})
        .catch(error=>{if(!controller.signal.aborted)console.warn('智能问数：读取回答核验状态失败',error);});
      return()=>controller.abort();
    },[sessionId,turn,seq]);
    if(quality?.status!=='partial')return null;
    return h('p',{role:'status',className:'nw-setting-status'},(quality.has_result??hasCards)?'部分完成 · 已保留查询结果':'本轮未取得可核实的查询结果');
  }
  function ProcessResults({items,forecastByRef}){
    // Collapsed by default; cards (and their iframes) render only when opened.
    const [open,setOpen]=React.useState(false);
    return h('div',{className:'nw-process-results'},h(DisclosureRow,{title:'查询过程（'+items.length+' 条）',open,expandable:true,expandOnRowClick:true,icon:h(IconDataOutlineMedium,{size:16}),onToggle:()=>setOpen(v=>!v)}),
      open&&h('div',{className:'nw-process-list'},items.map(value=>h(ToolCard,{key:value.result_ref,phase:'result',forecastClaimIds:forecastByRef.get(value.result_ref),block:{content:[{type:'text',text:JSON.stringify(value)}]}}))));
  }
  function AnswerResults({turn,seq,resume,sessionId}){
    const state=turn.data.get('natural-data-results');
    const items=(Array.isArray(state)?state:state?.results||[]).filter(item=>item.seq<=seq||item.failure);
    const quality=state?.ended&&state.attempted?h(AnswerQuality,{sessionId,turn:turn.turn,seq,hasCards:items.some(item=>!isExploratory(item.value))}):null;
    if(!items.length)return quality;
    const failed=!!state?.failed||items.some(item=>item.failure);
    const comparison=state?.comparisonPreview;
    const {main,process}=pickAnswerCards(items,state?.answer||'',failed,{userText:state?.userText||'',
      comparisonPreviewRef:comparison?.seq<=seq?comparison.ref:undefined});
    const forecastByRef=forecastClaimsByResult(items);
    if(!main.length&&!process.length)return quality;
    return h('section',{'aria-label':'本轮问数图表','data-natural-data-answer':true},main.map(value=>h(ToolCard,{key:value.result_ref,phase:'result',forecastClaimIds:forecastByRef.get(value.result_ref),block:{content:[{type:'text',text:JSON.stringify(value)}]}})),process.length>0&&h(ProcessResults,{items:process,forecastByRef}),failed&&resume&&h(ContinueAnswer,{resume:()=>resume(turn.turn)}),quality);
  }
  function apply(ctx){
    openNativeSession=id=>ctx.uiWorkspace.openSession(id);
    ctx.effect(()=>()=>{openNativeSession=null;});
    lifetime = new AbortController();
    const owner = lifetime;
    // 0.6.3: the settings entry is registered first and every other registration on
    // its own, so one failing step no longer takes the 智能问数 settings away.
    const step=(name,fn)=>{try{fn();}catch(error){console.error('智能问数界面初始化失败：'+name,error);}};
    // 0.6.4: hosts with a main-panel layout get the full 配置管理 page in the sidebar, so the
    // settings-dialog tab is only a fallback for hosts without one.
    if(typeof ctx.layout?.selectPanel!=='function')step('设置入口',()=>ctx.slots.inject('settings.section',()=>ctx.slots.register({name:'settings.section',id:'natural-data',label:()=> '智能问数',order:45},DataSettings)));
    step('生命周期',()=>ctx.effect(() => () => {owner.abort();resultCache.clear();resultMutationMarks.clear();}));
    step('样式',()=>ctx.effect(()=>{const style=document.createElement('style');style.dataset.naturalWorkUi='';style.textContent=css+administrationCss+consoleCss;document.head.appendChild(style);return()=>style.remove();}));
    if(typeof ctx.layout?.selectPanel==='function')step('配置管理入口',()=>{
      const panel='natural-data-administration';
      openAdministration=()=>ctx.layout.selectPanel(panel);
      ctx.effect(()=>()=>{openAdministration=null;});
      const NO_SNAPSHOT=()=>null;
      const MainConsole=(slotProps={})=>{
        const useWorkspaces=typeof slotProps.useWorkspaces==='function'?slotProps.useWorkspaces:NO_SNAPSHOT;
        const useSessions=typeof slotProps.useSessions==='function'?slotProps.useSessions:NO_SNAPSHOT;
        const wsSnap=useWorkspaces(s=>s);
        const sessSnap=useSessions(s=>s);
        const workspaces=Array.isArray(wsSnap?.items)?wsSnap.items.map(w=>({id:w.workspaceId,path:w.path,title:w.title})):[];
        const mainSession=sessSnap?.byId?Object.values(sessSnap.byId).find(s=>(s?.retainedBy?.mainView??0)>0):null;
        const matchedWorkspace=mainSession&&Array.isArray(wsSnap?.items)
          ?wsSnap.items.find(w=>Array.isArray(w.sessionIds)&&w.sessionIds.includes(mainSession.id)):null;
        const currentWorkspace=matchedWorkspace?.path||mainSession?.cwd||'';
        return h(Console,{onBack:()=>ctx.layout.selectPanel(null),currentWorkspace,workspaces});
      };
      const register=()=>{
        ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:panel},MainConsole));
        ctx.slots.inject('sidebar.panellist',()=>ctx.slots.register({name:'sidebar.panellist',id:panel,label:()=> '配置管理',order:95},({size})=>h(IconDataOutlineMedium,{size})));
      };
      // 0.6.3: a failed config read used to drop the entry silently. Retry a few times;
      // if it still fails, show the entry anyway so the page can say why.
      const delays=[1000,3000,10000];
      const load=attempt=>settingsRequest('config',undefined,{view:'manager'}).then(value=>{
        if(owner.signal.aborted||(!value.system_admin&&!value.projects.some(p=>['admin','editor'].includes(p.role))))return;
        register();
      }).catch(error=>{
        if(owner.signal.aborted||error?.name==='AbortError')return;
        if(attempt<delays.length){console.warn('智能问数：读取配置失败，稍后重试',error);setTimeout(()=>{if(!owner.signal.aborted)load(attempt+1);},delays[attempt]);return;}
        console.error('智能问数：读取配置失败，配置管理页会显示原因',error);
        register();
      });
      load(0);
    });
    step('结果事件',()=>{ctx.uiConversation.events.register(resultRequestEvents);ctx.uiConversation.events.register(resultDefinition);});
    step('结果卡',()=>ctx.slots.inject('conversation.chat.turnTail',()=>ctx.slots.register({name:'conversation.chat.turnTail',id:'natural-data-results',order:20,inject:sessionId=>({sessionId,resume:turn=>ctx.sessions.scope(sessionId).get('conversation').send('继续完成第'+turn+'轮未完成的回答。先复用本会话已成功的查询结果和工序回执；除非结果确实缺失或条件改变，不要重放查询。无法确认的解释请明确说明。')})},AnswerResults)));
    step('工具卡',()=>{for(const op of ['query','report','analyze','visualize']) ctx.slots.inject('tool.call.toolview',()=>ctx.slots.register({name:'tool.call.toolview',key:'data_'+op},ToolCard));});
  }
  return {inject,apply,ViewController};
}});
