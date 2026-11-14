"use client";

import { useMemo, useRef } from "react";
import { SEVERITY_META, ageSeconds, meters, shortId } from "@/lib/format";
import type { AStarRoute, AssignmentStatus, SimulationProposal, SimulationState } from "@/lib/types";

const STALE_S = 30; // backend observation freshness window

const TASK_COLOR: Record<AssignmentStatus, string> = {
  dispatched: "var(--warn)",
  delivered: "var(--info)",
  acknowledged: "var(--ok)",
  completed: "var(--faint)",
};

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  editMode: boolean;
  onToggleCell: (row: number, col: number) => void;
  now: number;
  /** When set, the next map click reports world meters (used to mark a responder's position). */
  onPlace?: ((x_m: number, y_m: number) => void) | null;
  /** Responder whose routes are emphasised (others dimmed). */
  focusResponderId?: string | null;
  /** Routes from the proposal that was just replaced (drawn faintly for comparison). */
  ghostRoutes?: AStarRoute[];
  /** Optional floor-plan image stretched over the map bounds. */
  floorPlan?: { url: string; opacity: number } | null;
  flashing?: Set<string>;
}

export function SceneMap({ state, proposal, selectedId, onSelect, editMode, onToggleCell, now, onPlace, focusResponderId, ghostRoutes = [], floorPlan, flashing }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const scene = state.scenario;
  const grid = scene.scene_map?.occupancy_grid ?? null;

  const bounds = useMemo(() => {
    if (grid) {
      return {
        x0: grid.origin.x_m,
        y0: grid.origin.y_m,
        w: grid.width * grid.resolution_m,
        h: grid.height * grid.resolution_m,
      };
    }
    const pts = [...scene.casualties.map((c) => c.position), ...scene.responders.map((r) => r.position)];
    const xs = pts.map((p) => p.x_m);
    const ys = pts.map((p) => p.y_m);
    const x0 = Math.min(0, ...xs) - 1;
    const y0 = Math.min(0, ...ys) - 1;
    return { x0, y0, w: Math.max(10, Math.max(...xs) - x0 + 2), h: Math.max(10, Math.max(...ys) - y0 + 2) };
  }, [grid, scene.casualties, scene.responders]);

  const PAD = 1.6;
  const X = (x: number) => x - bounds.x0;
  const Y = (y: number) => bounds.h - (y - bounds.y0); // +y is north/up on the shared map
  const path = (r: AStarRoute) => r.waypoints.map((p, i) => `${i ? "L" : "M"}${X(p.x_m)},${Y(p.y_m)}`).join(" ");

  const activeTasks = state.assignments.filter((a) => a.status !== "completed");
  const taskByCasualty = new Map(activeTasks.map((a) => [a.casualty_id, a]));
  const proposed = proposal && proposal.status !== "dispatched" ? proposal.assignments : [];
  const resolved = new Set(state.resolved_casualty_ids);
  const trackByCasualty = new Map(state.tracks.filter((t) => t.casualty_id).map((t) => [t.casualty_id!, t]));

  const ticks = (len: number, step: number) => Array.from({ length: Math.floor(len / step) + 1 }, (_, i) => i * step);

  return (
    <svg
      viewBox={`${-PAD} ${-PAD} ${bounds.w + PAD * 2} ${bounds.h + PAD * 2}`}
      ref={svgRef}
      className={`h-full w-full select-none ${onPlace ? "cursor-crosshair" : ""}`}
      role="img"
      aria-label="Scene map in meters"
      onClickCapture={(e) => {
        if (!onPlace || !svgRef.current) return;
        e.stopPropagation();
        const pt = svgRef.current.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        const m = svgRef.current.getScreenCTM();
        if (!m) return;
        const p = pt.matrixTransform(m.inverse());
        const x = Math.min(bounds.w, Math.max(0, p.x)) + bounds.x0;
        const y = bounds.h - Math.min(bounds.h, Math.max(0, p.y)) + bounds.y0;
        onPlace(Math.round(x * 10) / 10, Math.round(y * 10) / 10);
      }}
      onClick={() => !editMode && onSelect(null)}
    >
      <defs>
        <pattern id="hatch" width="0.4" height="0.4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="0.4" height="0.4" fill="var(--map-hatch-bg)" />
          <line x1="0" y1="0" x2="0" y2="0.4" stroke="var(--map-hatch-line)" strokeWidth="0.14" />
        </pattern>
        <marker id="arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="context-stroke" />
        </marker>
      </defs>

      {/* floor */}
      <rect x={0} y={0} width={bounds.w} height={bounds.h} fill="var(--map-floor)" stroke="var(--line-strong)" strokeWidth={0.06} />

      {floorPlan && (
        <image href={floorPlan.url} x={0} y={0} width={bounds.w} height={bounds.h} preserveAspectRatio="none" opacity={floorPlan.opacity} pointerEvents="none" />
      )}

      {/* occupancy grid */}
      {grid &&
        grid.cells.map((v, i) => {
          const col = i % grid.width;
          const row = Math.floor(i / grid.width);
          const x = grid.origin.x_m + col * grid.resolution_m;
          const y = grid.origin.y_m + (row + 1) * grid.resolution_m;
          const fill = v === 100 ? "url(#hatch)" : v === -1 ? "var(--map-unknown)" : "transparent";
          return (
            <rect
              key={i}
              x={X(x)}
              y={Y(y)}
              width={grid.resolution_m}
              height={grid.resolution_m}
              fill={fill}
              stroke="var(--map-cell-line)"
              strokeWidth={0.02}
              className={editMode ? "cursor-crosshair hover:fill-[var(--map-edit-hover)]" : undefined}
              onClick={
                editMode
                  ? (e) => {
                      e.stopPropagation();
                      onToggleCell(row, col);
                    }
                  : undefined
              }
            >
              {editMode && <title>{`cell r${row} c${col} · ${v === 100 ? "blocked" : v === -1 ? "unknown" : "free"}`}</title>}
            </rect>
          );
        })}

      {/* obstacle outlines */}
      {scene.scene_map?.obstacles.map((o) => (
        <polygon
          key={o.id}
          points={o.vertices.map((v) => `${X(v.x_m)},${Y(v.y_m)}`).join(" ")}
          fill="none"
          stroke="var(--map-obstacle)"
          strokeWidth={0.07}
          pointerEvents="none"
        />
      ))}

      {/* axis ticks (meters) */}
      <g className="mono" fontSize={0.42} fill="var(--faint)" pointerEvents="none">
        {ticks(bounds.w, 4).map((t) => (
          <text key={`x${t}`} x={t} y={bounds.h + 1.25} textAnchor="middle">
            {Math.round(bounds.x0 + t)}
          </text>
        ))}
        {ticks(bounds.h, 4).map((t) => (
          <text key={`y${t}`} x={-0.5} y={bounds.h - t + 0.15} textAnchor="end">
            {Math.round(bounds.y0 + t)}
          </text>
        ))}
        <text x={bounds.w} y={-0.45} textAnchor="end" letterSpacing={0.05}>
          {scene.frame_id} · meters · map {scene.scene_map?.revision ?? "none"}
        </text>
        <text x={0} y={-0.45} fill="var(--muted)">
          N ↑
        </text>
      </g>

      {/* active (dispatched) routes */}
      {activeTasks.map(
        (a) =>
          a.route && (
            <path
              key={a.id}
              d={path(a.route)}
              fill="none"
              stroke={TASK_COLOR[a.status]}
              strokeWidth={0.16}
              strokeLinejoin="round"
              strokeLinecap="round"
              markerEnd="url(#arrow)"
              opacity={focusResponderId && a.responder_id !== focusResponderId ? 0.4 : 0.95}
              pointerEvents="none"
            />
          ),
      )}

      {/* replaced proposal (for comparison) */}
      {ghostRoutes.map((g, i) => (
        <path
          key={`ghost-${i}`}
          d={path(g)}
          fill="none"
          stroke="var(--muted)"
          strokeOpacity={0.55}
          strokeWidth={0.09}
          strokeDasharray="0.15 0.3"
          strokeLinejoin="round"
          pointerEvents="none"
        />
      ))}

      {/* proposed (advisory) routes */}
      {proposed.map(
        (a) =>
          a.route && (
            <g key={`p-${a.casualty_id}`} pointerEvents="none" opacity={focusResponderId && a.responder_id !== focusResponderId ? 0.35 : 1}>
              <path d={path(a.route)} fill="none" stroke="var(--accent)" strokeOpacity={0.18} strokeWidth={0.5} strokeLinejoin="round" />
              <path
                d={path(a.route)}
                fill="none"
                stroke="var(--accent)"
                strokeWidth={0.12}
                strokeDasharray="0.5 0.5"
                strokeLinejoin="round"
                className="route-proposed"
                markerEnd="url(#arrow)"
              />
              {(() => {
                const mid = a.route.waypoints[Math.floor(a.route.waypoints.length / 3)];
                return (
                  <text x={X(mid.x_m) + 0.35} y={Y(mid.y_m) - 0.35} fontSize={0.5} fill="var(--accent)" className="mono">
                    {meters(a.route.distance_m)}
                  </text>
                );
              })()}
            </g>
          ),
      )}

      {/* CV tracks */}
      {state.tracks.map((t) => {
        const o = t.latest_observation;
        const stale = ageSeconds(o.observed_at, now) > STALE_S || (t.latest_observation.track?.state ?? "visible") !== "visible";
        const linked = !!t.casualty_id;
        const color = linked ? "var(--accent)" : "var(--warn)";
        const x = X(o.position.x_m);
        const y = Y(o.position.y_m);
        const s = 0.9;
        return (
          <g
            key={t.track_id}
            opacity={stale ? 0.45 : 0.35 + 0.65 * o.confidence}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(t.track_id);
            }}
            className="cursor-pointer"
          >
            <rect
              x={x - s}
              y={y - s}
              width={s * 2}
              height={s * 2}
              fill="none"
              stroke={color}
              strokeWidth={selectedId === t.track_id ? 0.1 : 0.05}
              strokeDasharray={stale ? "0.2 0.15" : "0.5 1.3"}
              strokeDashoffset={0.25}
            />
            {!linked && (
              <text x={x + s + 0.15} y={y - s + 0.4} fontSize={0.42} fill={color} className="mono">
                {t.camera?.local_track_id ?? shortId(t.track_id)} {Math.round(o.confidence * 100)}%{stale ? " STALE" : ""}
              </text>
            )}
            {t.camera?.location_basis === "zone_estimate" && (
              <circle cx={x} cy={y} r={1.6} fill="none" stroke={color} strokeWidth={0.03} strokeDasharray="0.15 0.15" />
            )}
          </g>
        );
      })}

      {/* casualties */}
      {scene.casualties.map((c) => {
        const sev = c.medical_assessment?.severity ?? "unassessed";
        const meta = SEVERITY_META[sev];
        const x = X(c.position.x_m);
        const y = Y(c.position.y_m);
        const sel = selectedId === c.id;
        const done = resolved.has(c.id);
        const task = taskByCasualty.get(c.id);
        const track = trackByCasualty.get(c.id);
        const stale = track ? ageSeconds(track.latest_observation.observed_at, now) > STALE_S : false;
        return (
          <g
            key={c.id}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(c.id);
            }}
            className="cursor-pointer"
            opacity={done ? 0.45 : 1}
          >
            {flashing?.has(c.id) && <circle cx={x} cy={y} r={0.9} fill="none" stroke="var(--warn)" strokeWidth={0.12} className="pulse-ring" />}
            {sev === "immediate" && !done && <circle cx={x} cy={y} r={0.6} fill="none" stroke={meta.color} strokeWidth={0.08} className="pulse-ring" />}
            {sel && <circle cx={x} cy={y} r={1.05} fill="none" stroke="var(--text)" strokeWidth={0.06} />}
            {sev === "unassessed" ? (
              <>
                <circle cx={x} cy={y} r={0.6} fill="var(--map-mark-bg)" stroke={meta.color} strokeWidth={0.09} strokeDasharray="0.22 0.14" />
                <text x={x} y={y + 0.24} fontSize={0.7} textAnchor="middle" fill={meta.color} fontWeight={700}>
                  ?
                </text>
              </>
            ) : (
              <>
                <circle cx={x} cy={y} r={0.6} fill={meta.color} stroke="var(--map-mark-outline)" strokeWidth={0.08} />
                {sev === "deceased" && <circle cx={x} cy={y} r={0.6} fill="none" stroke="#8b95a3" strokeWidth={0.06} />}
              </>
            )}
            {done && (
              <path d={`M${x - 0.28},${y} l0.2,0.22 l0.38,-0.44`} fill="none" stroke="var(--text)" strokeWidth={0.12} />
            )}
            <text x={x} y={y - 0.95} fontSize={0.5} textAnchor="middle" fill="var(--text)" className="mono" fontWeight={600}>
              {shortId(c.id)}
            </text>
            <text x={x} y={y + 1.25} fontSize={0.38} textAnchor="middle" fill={meta.color} className="mono" letterSpacing={0.04}>
              {meta.label.toUpperCase()}
              {stale ? " · STALE" : ""}
              {task ? ` · ${task.status.toUpperCase()}` : ""}
            </text>
          </g>
        );
      })}

      {/* responders */}
      {scene.responders.map((r) => {
        const x = X(r.position.x_m);
        const y = Y(r.position.y_m);
        const color = r.status === "available" ? "var(--accent)" : r.status === "busy" ? "var(--warn)" : "var(--faint)";
        const sel = selectedId === r.id || focusResponderId === r.id;
        const letter = r.display_name.replace(/^Mock Responder /, "").charAt(0).toUpperCase();
        return (
          <g
            key={r.id}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(r.id);
            }}
            className="cursor-pointer"
          >
            {sel && <rect x={x - 0.95} y={y - 0.95} width={1.9} height={1.9} rx={0.3} fill="none" stroke="var(--text)" strokeWidth={0.06} />}
            <rect x={x - 0.6} y={y - 0.6} width={1.2} height={1.2} rx={0.18} fill="var(--map-mark-bg)" stroke={color} strokeWidth={0.12} />
            <text x={x} y={y + 0.22} fontSize={0.62} textAnchor="middle" fill={color} fontWeight={700} className="mono">
              {letter}
            </text>
            {r.status === "offline" && (
              <path d={`M${x - 0.6},${y - 0.6} L${x + 0.6},${y + 0.6}`} stroke="var(--danger)" strokeWidth={0.1} />
            )}
            <text
              x={x < 3 ? x - 0.6 : x > bounds.w - 3 ? x + 0.6 : x}
              y={y - 0.85}
              fontSize={0.42}
              textAnchor={x < 3 ? "start" : x > bounds.w - 3 ? "end" : "middle"}
              fill={color}
              className="mono"
            >
              {shortId(r.id)} · {r.status.toUpperCase()}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
