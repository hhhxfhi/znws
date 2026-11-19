import {readFileSync} from 'node:fs';
import AgentPreset from '@deepseek-ai/dsh-agent-preset';
import {dshHomePath} from '@deepseek-ai/dsh-home-paths';
import * as dataHost from './packages/natural-data/index.js';
import {configureAnswerQualityDirectory} from './packages/natural-data/answer-guard.js';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createActivation} from './activation.js';
import {retirePreviousRows,refreshOwnClientModule} from './upgrade-compat.js';
import {normalizeBasePath} from './packages/natural-data/public-path.js';
import {installRequestReadiness} from './request-readiness.js';
import {installWorkspaceChangeScope} from './packages/natural-data/workspace-change-scope.js';

// Preset loader addresses survive replacement of payload directories. Their
// stable dispatchers read the active build and mount its immutable module.
// Package specifier exports survive pnpm isolated store directory replacement,
// avoiding dangling pointers to deleted .pnpm/<old-hash> physical directories.
const PRESET_ENTRIES = {
  'packages/natural-capabilities/index.js': '@natural-work/ask-data/capabilities',
  'packages/natural-data/context-budget.js': '@natural-work/ask-data/context-budget',
  'unavailable.js': '@natural-work/ask-data/unavailable'
};
const presetEntry = entry => {
  const specifier = PRESET_ENTRIES[entry];
  if (specifier) {
    try {
      import.meta.resolve(specifier);
      return specifier;
    } catch {}
  }
  const bundleRoot = new URL(import.meta.url.includes('/payload/') ? '../../' : './', import.meta.url);
  return fileURLToPath(new URL(entry, bundleRoot));
};

export const name = 'natural-ask-data';
export const inject = ['webServer', 'connection', 'sessionController', 'workspaceRegistry', 'agentPresets'];

export async function apply(ctx, config = {}) {
  retirePreviousRows(ctx.agentPresets);
  const publicBasePath = normalizeBasePath(config.publicBasePath ?? process.env.NATURAL_DATA_PUBLIC_BASE_PATH ?? '');
  const dataRoot = config.dataRoot || dshHomePath('plugin-data', 'ask-data');
  const lifetime = new AbortController();
  const answerQualityDirectory=join(dataRoot,'answer-quality');
  let presetFiber, presetConfig;
  const runtime=createActivation({dataRoot,python:config.python,signal:lifetime.signal,onReady:async()=>{
    if(presetFiber){presetFiber.update(presetConfig);await presetFiber.await();}
  }});
  ctx.effect(()=>async()=>{lifetime.abort();await runtime.stop();});
  configureAnswerQualityDirectory(answerQualityDirectory);
  installWorkspaceChangeScope(ctx);
  const runtimeKey='naturalDataRuntime_'+JSON.parse(readFileSync(new URL('./build-info.json',import.meta.url),'utf8')).build_id;
  ctx.provide(runtimeKey, Object.assign(runtime,{answerQualityDirectory}));
  dataHost.apply(ctx,{publicBasePath,call:runtime.call,answerQualityDirectory,dataRoot});
  // Retained sessions keep their old preset revision across a hot update.
  // Rebind at the next request boundary, before tools and policy are assembled.
  installRequestReadiness(ctx,runtime);
  // Keep diagnostic routes and a guarded session preset alive on failure. Only
  // validated active-engine contracts may populate the normal data tools.
  try{await runtime.ensureReady();}catch(error){if(lifetime.signal.aborted)return;console.error('[智能问数] 启动未就绪：',error.message);}
  if(lifetime.signal.aborted)return;
  presetConfig = {
    id: 'natural-data', name: '智能问数', order: 2,
    description: '查询和分析已授权的项目数据，在当前回答中展示图表及执行依据。',
    plugins: [
      {id: 'persona', name: '@deepseek-ai/dsh-persona', config: {prefix: '你是问数助手。通过受控数据工具完成查询与分析。'}},
      {id: 'natural-capabilities', name: presetEntry('packages/natural-capabilities/index.js'), config: {generation:runtimeKey,capabilities: config.capabilities || ['data', 'analysis', 'work'], toolProfile: config.toolProfile || 'compact'}},
      {id: 'tool-ask-user', name: '@deepseek-ai/dsh-tool-ask-user'},
      {id: 'compaction', name: 'cordis:group', group: true, isolate: {compaction: true}, config: [
        {id: 'compaction-basic', name: '@deepseek-ai/dsh-compaction-basic', config:{auto:false,headroomTokens:4096,maxTokens:config.compactionMaxTokens||4096}},
        {id:'context-budget',name:presetEntry('packages/natural-data/context-budget.js'),config:{generation:runtimeKey,contextWindowLimit:config.contextWindowLimit||70000,maxOutputTokens:config.maxOutputTokens||8192,maxOutputTokensThinking:config.maxOutputTokensThinking||12600,...(config.closingShare?{closingShare:config.closingShare}:{})}},
        {id: 'command-compact', name: '@deepseek-ai/dsh-command-compact'}]}
    ]
  };
  const unavailable={...presetConfig,plugins:[{id:'unavailable',name:presetEntry('unavailable.js'),config:{generation:runtimeKey}}]};
  presetFiber=ctx.plugin(AgentPreset,runtime.status().ready?presetConfig:unavailable);
  runtime.setClientStatus(refreshOwnClientModule(ctx,fileURLToPath(new URL('./client.js',import.meta.url))));
}

