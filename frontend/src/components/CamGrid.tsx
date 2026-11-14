"use client";

import { SEVERITY_META, ageSeconds, responderName, shortId } from "@/lib/format";
import type { Feed, FeedStatus } from "@/lib/feeds";
import type { Responder, SimulationProposal, SimulationState } from "@/lib/types";
import { VideoFeed } from "./VideoFeed";

const STATUS_COLOR = { available: "var(--accent)", busy: "var(--warn)", offline: "var(--faint)" } as const;

/** Camera feeds: one bodycam per firefighter, the building camera, and spare inputs to fill a 2×2 grid. */
export function cameraFeeds(state: SimulationState | null, responders: Responder[]): Feed[] {
  const spare = Math.max(0, 4 - responders.length - 1);
  return [
    ...responders.map((r) => ({
      id: `bodycam:${r.id}`,
      name: `${state ? responderName(state, r.id).toUpperCase() : r.id} · BODYCAM`,
      type: "bodycam" as const,
      responderId: r.id,
    })),
    { id: "surveillance", name: "BUILDING CAM", type: "building-camera" as const },
    ...Array.from({ length: spare }, (_, i) => ({ id: `extra-${i}`, name: `EXTRA FEED ${i + 1}`, type: "camera" as const })),
  ];
}

interface ViewProps {
  feed: Feed;
  state: SimulationState;
  proposal: SimulationProposal | null;
  now: number;
  primary: boolean;
  onStatus: (feedId: string, status: FeedStatus) => void;
}

/** The content of one camera feed. Rendered in the same place in the tree whether primary or minimized. */
export function CameraView({ feed, state, proposal, now, primary, onStatus }: ViewProps) {
  const common = { feedId: feed.id, title: feed.name, cameraId: feed.id, compact: !primary, onStatus, className: "h-full w-full" };

  if (feed.type === "bodycam" && feed.responderId) {
    const r = state.scenario.responders.find((x) => x.id === feed.responderId);
    const task = state.assignments.find((a) => a.responder_id === feed.responderId && a.status !== "completed");
    const prop =
      proposal && (proposal.status === "pending_approval" || proposal.status === "approved")
        ? proposal.assignments.find((a) => a.responder_id === feed.responderId)
        : undefined;
    const targetId = task?.casualty_id ?? prop?.casualty_id;
    const target = state.scenario.casualties.find((c) => c.id === targetId);
    const sev = target?.medical_assessment?.severity ?? "unassessed";
    return (
      <div className="relative h-full w-full">
        <VideoFeed {...common} audio />
        {r && (
          <div
            data-theme="dark"
            className={`pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-gradient-to-t from-black/90 to-transparent px-1.5 pb-1 pt-3 ${
              primary ? "text-[11px]" : "text-[9.5px]"
            }`}
          >
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: STATUS_COLOR[r.status] }} />
            <span className="mono uppercase text-white/80">{r.status}</span>
            {target && (
              <span className="mono ml-auto text-white/90">
                → {shortId(target.id)} <span style={{ color: SEVERITY_META[sev].color }}>{SEVERITY_META[sev].short}</span>
                <span className="text-white/50"> {task ? task.status : "proposed"}</span>
              </span>
            )}
          </div>
        )}
      </div>
    );
  }

  if (feed.type === "building-camera") {
    const tracked = state.tracks.filter(
      (t) => (t.latest_observation.track?.state ?? "visible") === "visible" && ageSeconds(t.latest_observation.observed_at, now) <= 30,
    ).length;
    return (
      <div className="relative h-full w-full">
        <VideoFeed
          {...common}
          overlay={
            <div className={`mono bg-gradient-to-t from-black/90 to-transparent px-1.5 pb-1 pt-3 text-white/80 ${primary ? "text-[11px]" : "text-[9.5px]"}`}>
              CV {tracked} tracked · detection only
            </div>
          }
        />
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <VideoFeed {...common} audio />
    </div>
  );
}
