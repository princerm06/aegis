"use client";

import { useMemo, useRef } from "react";
import { SEVERITY_META, ageSeconds, meters, shortId, trackLabel } from "@/lib/format";
import { getInvalidatedAssignments, isRouteStale } from "@/lib/routeInvalidation";
import type { FloorOutline } from "@/lib/floorOutlines";
import type { AStarRoute, AssignmentStatus, SimulationProposal, SimulationState } from "@/lib/types";

const STALE_S = 30; // backend observation freshness window

/** Dark halo behind map labels so text stays readable over boxes, routes, and floor plans. */
const HALO = { paintOrder: "stroke", stroke: "var(--map-floor)", strokeWidth: 0.14, strokeLinejoin: "round" } as const;

const RESPONDER_SHORT = { available: "AVAIL", busy: "BUSY", offline: "OFF" } as const;

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
  /** Only these entity ids (casualties, tracks, responders) are drawn; null/undefined draws everyone. */
  visibleIds?: Set<string> | null;
  /** Draw the occupancy grid, obstacles and routes (false on floors the routing grid doesn't describe). */
  showRouting?: boolean;
  /** Floor name shown in the map caption. */
  floorLabel?: string;
  /** People the planner cannot route to/from (standing on a wall or blocked cell): labelled NO ROUTE. */
  noRouteIds?: Set<string>;
  /** Simplified building outline drawn on the floor (cells over the map bounds, row 0 = north edge). */
  outline?: FloorOutline | null;
}

export function SceneMap({ state, proposal, selectedId, onSelect, editMode, onToggleCell, now, onPlace, focusResponderId, ghostRoutes = [], floorPlan, flashing, visibleIds, showRouting = true, floorLabel, outline, noRouteIds }: Props) {
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

  const PAD = 1.3; // framing only; X/Y coordinate mapping is untouched
  const X = (x: number) => x - bounds.x0;
  const Y = (y: number) => bounds.h - (y - bounds.y0); // +y is north/up on the shared map
  const path = (r: AStarRoute) => r.waypoints.map((p, i) => `${i ? "L" : "M"}${X(p.x_m)},${Y(p.y_m)}`).join(" ");

  const vis = (id: string) => !visibleIds || visibleIds.has(id);
  const activeTasks = state.assignments.filter((a) => a.status !== "completed");
  const drawnTasks = showRouting ? activeTasks.filter((a) => vis(a.casualty_id)) : [];
  const taskByCasualty = new Map(activeTasks.map((a) => [a.casualty_id, a]));
  const invalidated = useMemo(() => getInvalidatedAssignments(state), [state]);
  const invalidatedIds = useMemo(() => new Set(invalidated.map((i) => i.assignment.id)), [invalidated]);
  const alternatives = useMemo(
    () => invalidated.map((i) => i.alternativeRoute).filter((r): r is AStarRoute => !!r),
    [invalidated],
  );
  const mapRevision = scene.scene_map?.revision;
  const proposed = showRouting && proposal && proposal.status !== "dispatched" ? proposal.assignments.filter((a) => vis(a.casualty_id)) : [];
  const resolved = new Set(state.resolved_casualty_ids);
  const trackByCasualty = new Map(state.tracks.filter((t) => t.casualty_id).map((t) => [t.casualty_id!, t]));

  // Fine grids (building walls at 0.25 m) are drawn as one path of blocked cells, not a rect per cell.
  const fine = !!grid && grid.width * grid.height > 2500;
  // Cells that are walls in the drawn outline are already shown as outline lines; only draw extra
  // blocked cells (operator "Block route" edits) so the map stays clean.
  const fineBlocked = useMemo(() => {
    if (!grid || !fine) return "";
    const r = grid.resolution_m;
    const drawn = (row: number, col: number) =>
      !!outline && outline.walls.length === grid.height && outline.walls[grid.height - 1 - row]?.[col] === "#";
    const extra = (row: number, col: number) => grid.cells[row * grid.width + col] !== 0 && !drawn(row, col);
    let d = "";
    for (let row = 0; row < grid.height; row++) {
      for (let col = 0; col < grid.width; col++) {
        if (!extra(row, col)) continue;
        let end = col;
        while (end + 1 < grid.width && extra(row, end + 1)) end++;
        const x = grid.origin.x_m + col * r - bounds.x0;
        const y = bounds.h - (grid.origin.y_m + (row + 1) * r - bounds.y0);
        d += `M${x},${y}h${(end - col + 1) * r}v${r}h${-(end - col + 1) * r}z`;
        col = end;
      }
    }
    return d;
  }, [grid, fine, bounds, outline]);
  const fineCellAt = (clientX: number, clientY: number): [number, number] | null => {
    if (!grid || !svgRef.current) return null;
    const pt = svgRef.current.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const m = svgRef.current.getScreenCTM();
    if (!m) return null;
    const p = pt.matrixTransform(m.inverse());
    const col = Math.floor((p.x + bounds.x0 - grid.origin.x_m) / grid.resolution_m);
    const row = Math.floor((bounds.h - p.y + bounds.y0 - grid.origin.y_m) / grid.resolution_m);
    return col >= 0 && col < grid.width && row >= 0 && row < grid.height ? [row, col] : null;
  };

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

      {/* building outline: footprint fill, room outlines, perimeter */}
      {outline && (
        <g pointerEvents="none" data-outline-cells={outline.cells} data-outline-rooms={outline.rooms}>
          <path d={outline.fill} fill="var(--map-outline-fill)" />
          {!showRouting && (
            <g stroke="var(--map-cell-line)" strokeWidth={0.02}>
              {ticks(bounds.w, 1).map((t) => (
                <line key={`gx${t}`} x1={t} y1={0} x2={t} y2={bounds.h} />
              ))}
              {ticks(bounds.h, 1).map((t) => (
                <line key={`gy${t}`} x1={0} y1={t} x2={bounds.w} y2={t} />
              ))}
            </g>
          )}
          <path d={outline.roomEdges} fill="none" stroke="var(--map-room-line)" strokeWidth={0.04} strokeLinecap="square" />
          <path d={outline.perimeter} fill="none" stroke="var(--map-outline)" strokeWidth={0.1} strokeLinecap="square" />
        </g>
      )}

      {/* occupancy grid */}
      {showRouting && grid && fine && (
        <g data-routing-grid="fine" data-blocked-cells={grid.cells.filter((v) => v !== 0).length}>
          <path d={fineBlocked} fill="url(#hatch)" stroke="var(--map-obstacle)" strokeWidth={0.03} pointerEvents="none" />
          {editMode && (
            <rect
              x={0}
              y={0}
              width={bounds.w}
              height={bounds.h}
              fill="transparent"
              className="cursor-crosshair"
              onClick={(e) => {
                e.stopPropagation();
                const cell = fineCellAt(e.clientX, e.clientY);
                if (cell) onToggleCell(cell[0], cell[1]);
              }}
            />
          )}
        </g>
      )}

      {showRouting &&
        grid &&
        !fine &&
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
      {showRouting && scene.scene_map?.obstacles.map((o) => (
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
          <text key={`x${t}`} x={t} y={bounds.h + 0.95} textAnchor="middle">
            {Math.round(bounds.x0 + t)}
          </text>
        ))}
        {ticks(bounds.h, 4).map((t) => (
          <text key={`y${t}`} x={-0.5} y={bounds.h - t + 0.15} textAnchor="end">
            {Math.round(bounds.y0 + t)}
          </text>
        ))}
        <text x={bounds.w} y={-0.45} textAnchor="end" letterSpacing={0.05}>
          {floorLabel ? `${floorLabel} · ` : ""}
          {scene.frame_id} · meters · {showRouting ? `map ${scene.scene_map?.revision ?? "none"}` : "no routing grid on this floor"}
        </text>
        <text x={0} y={-0.45} fill="var(--muted)">
          N ↑
        </text>
      </g>

      {/* active (dispatched) routes: invalidated and stale routes render as history */}
      {drawnTasks.map((a) => {
        if (!a.route) return null;
        const isInvalid = invalidatedIds.has(a.id);
        const stale = !isInvalid && isRouteStale(a.route, mapRevision);
        const dimmed = focusResponderId && a.responder_id !== focusResponderId;
        if (isInvalid) {
          return (
            <g key={a.id} pointerEvents="none" opacity={dimmed ? 0.45 : 0.9}>
              <path
                d={path(a.route)}
                fill="none"
                stroke="var(--danger)"
                strokeWidth={0.16}
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray="0.35 0.25"
                opacity={0.85}
              />
              <path
                d={path(a.route)}
                fill="none"
                stroke="var(--map-mark-bg)"
                strokeWidth={0.05}
                strokeLinejoin="round"
                opacity={0.7}
              />
            </g>
          );
        }
        if (stale) {
          return (
            <path
              key={a.id}
              d={path(a.route)}
              fill="none"
              stroke={TASK_COLOR[a.status]}
              strokeWidth={0.12}
              strokeLinejoin="round"
              strokeLinecap="round"
              strokeDasharray="0.4 0.3"
              markerEnd="url(#arrow)"
              opacity={dimmed ? 0.3 : 0.55}
              pointerEvents="none"
            />
          );
        }
        return (
          <path
            key={a.id}
            d={path(a.route)}
            fill="none"
            stroke={TASK_COLOR[a.status]}
            strokeWidth={0.16}
            strokeLinejoin="round"
            strokeLinecap="round"
            markerEnd="url(#arrow)"
            opacity={dimmed ? 0.4 : 0.95}
            pointerEvents="none"
          />
        );
      })}

      {/* advisory alternatives for invalidated routes (require new approval) */}
      {showRouting && alternatives.map((r, i) => (
        <path
          key={`alt-${i}`}
          d={path(r)}
          fill="none"
          stroke="var(--info)"
          strokeWidth={0.12}
          strokeDasharray="0.5 0.35"
          strokeLinejoin="round"
          markerEnd="url(#arrow)"
          opacity={0.9}
          pointerEvents="none"
        />
      ))}

      {/* replaced proposal (for comparison) */}
      {showRouting && ghostRoutes.map((g, i) => (
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
                  <text x={X(mid.x_m) + 0.35} y={Y(mid.y_m) - 0.35} fontSize={0.5} fill="var(--accent)" className="mono" {...HALO}>
                    {meters(a.route.distance_m)}
                  </text>
                );
              })()}
            </g>
          ),
      )}

      {/* CV track boxes (labels render in a later pass, on top of markers) */}
      {state.tracks.filter((t) => vis(t.track_id)).map((t) => {
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
            data-entity="track"
            opacity={stale ? 0.55 : 0.35 + 0.65 * o.confidence}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(t.track_id);
            }}
            className="cursor-pointer"
          >
            <title>{`${t.track_id} · ${Math.round(o.confidence * 100)}%${stale ? " · stale" : ""}`}</title>
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
            {t.camera?.location_basis === "zone_estimate" && (
              <circle cx={x} cy={y} r={1.6} fill="none" stroke={color} strokeWidth={0.03} strokeDasharray="0.15 0.15" />
            )}
          </g>
        );
      })}

      {/* casualties */}
      {scene.casualties.filter((c) => vis(c.id)).map((c) => {
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
            data-entity="casualty"
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
            <text x={x} y={y - 0.95} fontSize={0.5} textAnchor="middle" fill="var(--text)" className="mono" fontWeight={600} {...HALO}>
              {shortId(c.id)}
            </text>
            <text x={x} y={y + 1.25} fontSize={0.38} textAnchor="middle" fill={meta.color} className="mono" letterSpacing={0.04} {...HALO}>
              {meta.label.toUpperCase()}
              {stale ? " · STALE" : ""}
              {task ? ` · ${task.status.toUpperCase()}` : ""}
              {showRouting && noRouteIds?.has(c.id) ? " · NO ROUTE (ON WALL)" : ""}
            </text>
          </g>
        );
      })}

      {/* unlinked CV labels: own pass so text sits above markers; staggered sides, flipped below near the top edge */}
      <g pointerEvents="none" className="mono" fontSize={0.42}>
        {state.tracks.filter((t) => vis(t.track_id)).map((t, i) => {
          if (t.casualty_id) return null;
          const o = t.latest_observation;
          const stale = ageSeconds(o.observed_at, now) > STALE_S || (o.track?.state ?? "visible") !== "visible";
          const x = X(o.position.x_m);
          const y = Y(o.position.y_m);
          const s = 0.9;
          const left = i % 2 === 1;
          const belowBox = y < 2.0;
          return (
            <text
              key={t.track_id}
              x={left ? x - s - 0.15 : x + s + 0.15}
              y={belowBox ? y + s + 0.55 : y - s + 0.4}
              textAnchor={left ? "end" : "start"}
              fill="var(--warn)"
              opacity={stale ? 0.75 : 0.5 + 0.5 * o.confidence}
              {...HALO}
            >
              {trackLabel(t)} {Math.round(o.confidence * 100)}%{stale ? " STALE" : ""}
            </text>
          );
        })}
      </g>

      {/* responders */}
      {scene.responders.filter((r) => vis(r.id)).map((r) => {
        const x = X(r.position.x_m);
        const y = Y(r.position.y_m);
        const color = r.status === "available" ? "var(--accent)" : r.status === "busy" ? "var(--warn)" : "var(--faint)";
        const sel = selectedId === r.id || focusResponderId === r.id;
        const letter = r.display_name.replace(/^Mock Responder /, "").charAt(0).toUpperCase();
        const nearLeft = x < 3;
        const nearRight = x > bounds.w - 3;
        const nearTop = y < 2.2;
        return (
          <g
            key={r.id}
            data-entity="responder"
            onClick={(e) => {
              e.stopPropagation();
              onSelect(r.id);
            }}
            className="cursor-pointer"
          >
            <title>{`${r.display_name} · ${r.status}`}</title>
            {sel && <rect x={x - 0.95} y={y - 0.95} width={1.9} height={1.9} rx={0.3} fill="none" stroke="var(--text)" strokeWidth={0.06} />}
            <rect x={x - 0.6} y={y - 0.6} width={1.2} height={1.2} rx={0.18} fill="var(--map-mark-bg)" stroke={color} strokeWidth={0.12} />
            <text x={x} y={y + 0.22} fontSize={0.62} textAnchor="middle" fill={color} fontWeight={700} className="mono">
              {letter}
            </text>
            {r.status === "offline" && (
              <path d={`M${x - 0.6},${y - 0.6} L${x + 0.6},${y + 0.6}`} stroke="var(--danger)" strokeWidth={0.1} />
            )}
            <text
              x={nearLeft ? x - 0.6 : nearRight ? x + 0.6 : x}
              y={nearTop ? y + 1.25 : y - 0.85}
              fontSize={0.42}
              textAnchor={nearLeft ? "start" : nearRight ? "end" : "middle"}
              fill={color}
              className="mono"
              {...HALO}
            >
              {shortId(r.id)} · {RESPONDER_SHORT[r.status]}
              {showRouting && noRouteIds?.has(r.id) ? " · NO ROUTE (ON WALL)" : ""}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

