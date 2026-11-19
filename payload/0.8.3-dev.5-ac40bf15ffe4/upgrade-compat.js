// Retired presets keep sessions alive, but must never reactivate old modules
// during a package replacement. Limit the change to this plugin's retired rows;
// do not alter the host's reload implementation or any active preset.
export function retirePreviousRows(registry) {
  let count=0;
  for(const generation of registry.generations?.values?.()||[]) {
    if(!generation.retired||generation.mount.presetId!=='natural-data')continue;
    for(const entry of generation.mount.tree.entries()) {
      if(entry.options.group)continue;
      entry.options={...entry.options,disabled:true};
      count++;
    }
  }
  return count;
}

// Compatibility adapter for the inspected alpha.2 registry. Unsupported shapes
// are reported, never allowed to make the entire plugin fail activation.
export function refreshOwnClientModule(ctx, expectedPath) {
  const id='@natural-work/ask-data';
  let modules, saved;
  try {
    modules=ctx.get('clientModules');
    if(!modules)return {status:'unavailable',reason:'clientModules service absent'};
    if(typeof modules.clientPath!=='function')return {status:'unsupported',reason:'No clientPath API'};
    if(modules.clientPath(id)===expectedPath) {
      if(typeof modules.rebuilt==='function')modules.rebuilt(id);
      return {status:'current',path:expectedPath,browser_verified:false};
    }
    const entry=ctx.fiber?.entry;
    if(!entry||entry.options?.name!==id||!entry.parent?.tree?.ctx?.baseUrl)
      return {status:'unsupported',reason:'No owning Loader entry'};
    const maps=['pkgMeta','table','sources','responses','batchResponses','previousBatchResponses'];
    if(maps.some(k=>!(modules[k] instanceof Map))||!(modules.dirty instanceof Set)||
       ['sourceKey','flush','graph'].some(k=>typeof modules[k]!=='function'))
      return {status:'unsupported',reason:'Unrecognized registry shape; client refresh not verified'};
    const key=modules.sourceKey(id,entry.parent.tree.ctx.baseUrl);
    const meta=modules.pkgMeta.get(key),record=modules.table.get(id);
    if(meta?.packageName!==id||(record&&record.sourceKey!==key))
      return {status:'unsupported',reason:'Registry ownership mismatch'};
    saved={maps:Object.fromEntries(maps.map(k=>[k,new Map(modules[k])])),dirty:new Set(modules.dirty),composed:modules.composed};
    modules.pkgMeta.delete(key);modules.table.delete(id);modules.dirty.add(id);
    const failures=[];modules.flush(error=>failures.push(error));
    if(failures.length||modules.clientPath(id)!==expectedPath)throw Error('Client metadata refresh did not resolve the active payload');
    return {status:'refreshed',path:expectedPath,graph_revision:modules.graph()?.rev,browser_verified:false};
  } catch(error) {
    if(saved) {
      for(const [k,map] of Object.entries(saved.maps))modules[k]=map;
      modules.dirty=saved.dirty;modules.composed=saved.composed;
    }
    return {status:'failed',reason:'Client module refresh failed; see host diagnostics',browser_verified:false};
  }
}
