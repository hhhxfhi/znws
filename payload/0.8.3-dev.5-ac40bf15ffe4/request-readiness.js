// Prompt/tool providers are collected BEFORE agent/request (alpha.2).
// After rebinding, discard that assembly and collect again in the same turn.
export function installRequestReadiness(ctx, runtime) {
  const rebuilding = new WeakSet();
  ctx.on('system-prompt/assemble', async (_assembly, context, next) => {
    const {agent, signal} = context;
    if (!agent || agent.session?.header?.agentPreset !== 'natural-data') return next();
    const before = ctx.agentPresets.generationFor?.(agent.ctx);
    const attempts = runtime.status().attempts;
    const recovering = !runtime.status().ready;
    await runtime.ensureReady({probe:true});
    signal?.throwIfAborted();
    if (!recovering && attempts===runtime.status().attempts && !before?.retired) return next();
    if (rebuilding.has(agent)) throw Error('PRESET_REBIND_FAILED: 当前工具尚未更新，已停止本次执行。');
    if (typeof ctx.agentPresets.recompose !== 'function' || !agent.ctx.systemPrompt?.assemble)
      throw Error('PRESET_REBIND_UNSUPPORTED: 当前宿主不支持安全重组会话，请更新宿主后继续原会话。');
    rebuilding.add(agent);
    try {
      await ctx.agentPresets.recompose(agent.ctx, 'natural-data');
      signal?.throwIfAborted();
      const fresh = await agent.ctx.systemPrompt.assemble(context);
      if (!fresh.tools?.some(t => t.name === 'data_catalog'))
        throw Error('PRESET_REBIND_FAILED: 重组后缺少问数工具，未调用模型。');
      return fresh;
    } finally { rebuilding.delete(agent); }
  }, {prepend:true});
}
