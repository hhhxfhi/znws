import {isAbsolute, relative, resolve, sep} from 'node:path';

// These tools never deliver a workspace file. Unknown tools, including
// explicitly presented files and future exporters, keep native summaries.
const queryTools = new Set([
  'data_catalog', 'data_query', 'data_sql_query', 'data_metric_query', 'data_report',
  'data_plan', 'data_plan_query', 'data_semantic_plan', 'data_analyze', 'data_visualize',
  'data_derive_aggregation', 'data_inspect', 'analysis_list', 'analysis_compute',
  'analysis_field_statistics', 'analysis_method', 'analysis_run', 'ask_user_question'
]);

// Immutable payload paths load a fresh module on hot upgrade.
const wrapperState = Symbol.for('@natural-work/ask-data/workspace-change-summary');

function outsideWorkspace(cwd, file) {
  if (typeof file?.path !== 'string' || !file.path || file.path.includes('\0')) return false;
  const path = relative(cwd, resolve(cwd, file.path));
  return path === '..' || path.startsWith('..' + sep) || isAbsolute(path);
}

function replacesHistory(op, turnStartSeq) {
  return op?.op === 'replace' && Object.keys(op).length === 3
    && Object.hasOwn(op, 'op') && Object.hasOwn(op, 'startSeq') && Object.hasOwn(op, 'endSeq')
    && Number.isSafeInteger(op.startSeq) && Number.isSafeInteger(op.endSeq)
    && op.startSeq >= 0 && !Object.is(op.startSeq, -0) && !Object.is(op.endSeq, -0)
    && op.startSeq <= op.endSeq && op.endSeq < turnStartSeq;
}

function unattributedSummary(summary, session, seq) {
  if (!session || typeof session.header?.cwd !== 'string' || !isAbsolute(session.header.cwd)
      || session.header.cwd !== summary.cwd || !Array.isArray(summary.files)
      || !summary.files.length || summary.files.length !== summary.total
      || !summary.files.every(file => outsideWorkspace(summary.cwd, file))) return false;

  const events = session.snapshotEvents();
  const announcement = events.find(event => event.seq === seq && event.type === 'workspace/changes');
  if (!announcement || announcement.data?.turn !== summary.turn) return false;
  const start = events.findLastIndex(event => event.seq < seq && event.type === 'turn/start');
  if (start < 0 || events[start].data?.turn !== summary.turn) return false;

  const selection = events.findLast(event => event.seq <= seq && event.type === 'agent-preset/selected');
  if ((selection ? selection.data?.agentPreset : session.header.agentPreset) !== 'natural-data') return false;

  const calls = new Set();
  let completed = false;
  // Read the entire observed turn, including work after an early summary.
  // Any real export or unknown tool keeps every native summary for the turn.
  for (const event of events.slice(start + 1)) {
    if (event.type === 'turn/start') break;
    if (event.type === 'agent-preset/selected' || event.type === 'deliverables/presented') return false;
    if (event.type === 'tool/call') {
      if (event.data?.turn !== summary.turn || !queryTools.has(event.data.name)
          || typeof event.data.callId !== 'string' || !event.data.callId) return false;
      calls.add(event.data.callId);
    }
    if (event.type === 'tool/result' && event.seq < seq) {
      // Host context compaction can replace earlier turns' results while this
      // turn is running. Those copies are not tool activity from this turn.
      if (event.surfaceOp !== undefined && event.surfaceOp !== 'append') {
        if (replacesHistory(event.surfaceOp, events[start].seq)) continue;
        return false;
      }
      const id = event.data?.message?.source?.callId || event.data?.message?.toolCallId;
      if (event.data?.turn !== summary.turn || !calls.has(id)) return false;
      completed = true;
    }
    if (event.type === 'turn/end') break;
  }
  return completed;
}

/** Filter the host's read-only summary lookup without changing its event log. */
export function installWorkspaceChangeScope(ctx) {
  if (typeof ctx.inject === 'function') {
    ctx.inject(['workspaceChanges', 'sessions'], scopeWorkspaceChanges);
  } else {
    scopeWorkspaceChanges(ctx);
  }
}

function scopeWorkspaceChanges(ctx) {
  const changes = ctx.get?.('workspaceChanges');
  const sessions = ctx.get?.('sessions');
  if (typeof sessions?.get !== 'function' || typeof ctx.effect !== 'function') return;
  const descriptor = changes && Object.getOwnPropertyDescriptor(changes, 'summary');
  if (!descriptor?.writable || typeof descriptor.value !== 'function') return;

  ctx.effect(() => {
    const previous = changes.summary;
    if (typeof previous !== 'function') return;
    const base = previous[wrapperState]?.base ?? previous;
    const state = {active: true, previous, base};
    function scoped(sessionId, seq) {
      const summary = state.base.call(this, sessionId, seq);
      try {
        if (state.active && summary && unattributedSummary(summary, sessions.get(sessionId), seq)) return undefined;
      } catch { /* An unrecognized host shape must preserve its summary. */ }
      return summary;
    }
    Object.defineProperty(scoped, wrapperState, {value:state});
    changes.summary = scoped;
    return () => {
      state.active = false;
      if (changes.summary !== scoped) return;
      let restored = state.previous;
      while (restored[wrapperState]?.active === false) restored = restored[wrapperState].previous;
      changes.summary = restored;
    };
  }, 'natural-data: scope unrelated workspace changes');
}
