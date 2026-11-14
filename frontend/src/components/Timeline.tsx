"use client";

import { useState } from "react";
import { clock, describeEvent } from "@/lib/format";
import type { EventEnvelope } from "@/lib/types";
import { Empty, PanelHeader } from "./ui";

const NOISY = new Set(["simulation.replan_requested", "simulation.recommendation_computed", "simulation.proposal_superseded"]);

const TONE_COLOR = {
  neutral: "var(--faint)",
  accent: "var(--accent)",
  warn: "var(--warn)",
  ok: "var(--ok)",
  danger: "var(--danger)",
} as const;

export function Timeline({ events }: { events: EventEnvelope[] }) {
  const [all, setAll] = useState(false);
  const shown = (all ? events : events.filter((e) => !NOISY.has(e.type))).slice().reverse();

  return (
    <section className="panel flex min-h-0 flex-col">
      <PanelHeader
        title="Incident timeline"
        sub={`${events.length} events`}
        right={
          <label className="mono flex cursor-pointer items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
            system
          </label>
        }
      />
      <ol className="scrollbar-thin min-h-0 flex-1 overflow-y-auto py-1">
        {shown.length === 0 && <Empty>Waiting for events…</Empty>}
        {shown.map((e) => {
          const d = describeEvent(e);
          return (
            <li key={e.event_id} className="grid grid-cols-[56px_10px_1fr] items-start gap-2 px-3 py-1 hover:bg-panel-2">
              <time className="mono pt-px text-[10.5px] text-faint">{clock(e.occurred_at)}</time>
              <span className="mt-1.5 h-1.5 w-1.5 rounded-full" style={{ background: TONE_COLOR[d.tone] }} />
              <div className="min-w-0 text-[11.5px] leading-snug">
                <span className={d.tone === "neutral" ? "text-muted" : "text-text"}>{d.title}</span>
                {d.detail && <span className="mono ml-1.5 text-[10.5px] text-faint">{d.detail}</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
