import { createInitialState } from '../../lib/sandbox/engine';
import { SandboxMap } from './SandboxMap';

export function SandboxPreview() {
  return (
    <div className="lp-card overflow-hidden rounded-2xl border border-line bg-panel p-4 text-text sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <span className="mono text-xs uppercase tracking-widest text-accent">Command Center / Preview</span>
        <span className="rounded-full border border-line px-2 py-1 text-xs text-muted">Synthetic · read-only</span>
      </div>
      <SandboxMap state={createInitialState()} />
      <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-line pt-3 text-xs text-muted"><span>4 unconfirmed casualties</span><span>2 virtual responders</span><span>No assignments dispatched</span></div>
    </div>
  );
}
