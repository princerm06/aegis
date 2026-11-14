"use client";

import { useState, type PointerEvent } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { useEnable3D } from "./motion";

/**
 * Illustrative scene for the hero. Every value is hand-placed demo data and is labelled
 * SIMULATED in the UI — it is not perception output or backend state.
 * Scale: 20 px = 1 m, origin bottom-left of the scene frame (40, 480).
 */
const ORIGIN_X = 40;
const ORIGIN_Y = 480;
const toM = (px: number, py: number) => [(px - ORIGIN_X) / 20, (ORIGIN_Y - py) / 20] as const;

type Entity = { id: string; kind: "casualty" | "responder"; x: number; y: number; status: string; assignment: string };

const ENTITIES: Entity[] = [
  { id: "C-001", kind: "casualty", x: 230, y: 150, status: "Not assessed — awaiting human", assignment: "Proposed: R-01 · rev 1 · awaiting approval" },
  { id: "C-002", kind: "casualty", x: 360, y: 405, status: "Not assessed — awaiting human", assignment: "None proposed" },
  { id: "C-003", kind: "casualty", x: 405, y: 225, status: "Not assessed — awaiting human", assignment: "None proposed" },
  { id: "R-01", kind: "responder", x: 110, y: 420, status: "Available", assignment: "Proposed route to C-001 · awaiting approval" },
  { id: "R-02", kind: "responder", x: 580, y: 215, status: "Available", assignment: "Unassigned" },
];
const ROUTE = "M110 420 V250 H230 V170";
const ROUTE_IDS = new Set(["R-01", "C-001", "route"]);

// Faint topographic contours for the background layer (deterministic).
const TOPO = Array.from({ length: 6 }, (_, k) => {
  const cx = 360, cy = 270, r = 70 + k * 46;
  const pts = Array.from({ length: 49 }, (_, i) => {
    const a = (i / 48) * Math.PI * 2;
    const wobble = 1 + 0.08 * Math.sin(a * 3 + k) + 0.05 * Math.cos(a * 5 - k * 0.7);
    return `${(cx + Math.cos(a) * r * 1.45 * wobble).toFixed(1)},${(cy + Math.sin(a) * r * 0.82 * wobble).toFixed(1)}`;
  });
  return `M${pts.join(" L")}Z`;
});
const COORDS = [
  { x: "3%", y: "8%", t: "ORIGIN 0,0 · LOCAL" },
  { x: "80%", y: "4%", t: "N ↑ · 20 px/m" },
  { x: "86%", y: "90%", t: "FLOOR 1 · z 0.0 m" },
  { x: "1%", y: "92%", t: "GRID 32×22 m" },
];

function EntityMark({ e, on, onFocus }: { e: Entity; on: boolean; onFocus: (id: string | null) => void }) {
  const [mx, my] = toM(e.x, e.y);
  const label = `${e.id}, ${e.kind === "casualty" ? "detected person" : "responder"}, ${e.status}, ${e.assignment}. Simulated.`;
  return (
    <g
      className="lp-entity"
      data-on={on}
      tabIndex={0}
      role="img"
      aria-label={label}
      onPointerEnter={() => onFocus(e.id)}
      onPointerLeave={() => onFocus(null)}
      onFocus={() => onFocus(e.id)}
      onBlur={() => onFocus(null)}
      onClick={() => onFocus(e.id)}
    >
      <circle cx={e.x} cy={e.y} r="22" fill="transparent" />
      {e.kind === "casualty" ? (
        <>
          <circle cx={e.x} cy={e.y} r="7" fill="none" stroke="var(--sev-unassessed)" strokeOpacity="0.6" className="pulse-ring" />
          <path
            d={`M${e.x - 15} ${e.y - 10}v-5h5 M${e.x + 10} ${e.y - 15}h5v5 M${e.x + 15} ${e.y + 10}v5h-5 M${e.x - 10} ${e.y + 15}h-5v-5`}
            stroke="var(--accent)"
            strokeWidth={on ? 1.8 : 1.2}
            fill="none"
          />
          <circle cx={e.x} cy={e.y} r="5" fill="var(--bg)" stroke="var(--sev-unassessed)" strokeWidth="1.5" strokeDasharray="2 1.6" />
          <circle cx={e.x} cy={e.y} r="1.6" fill="var(--sev-unassessed)" />
        </>
      ) : (
        <>
          <circle cx={e.x} cy={e.y} r={on ? 14 : 11} fill="var(--accent)" opacity="0.12" />
          <path d={`M${e.x} ${e.y - 7} L${e.x + 6.5} ${e.y + 5} L${e.x - 6.5} ${e.y + 5} Z`} fill="var(--accent)" />
        </>
      )}
      <text x={e.x + 20} y={e.y - 3} className="mono" fontSize="10.5" fill={e.kind === "responder" ? "var(--accent)" : "var(--text)"}>
        {e.id}
      </text>
      <text x={e.x + 20} y={e.y + 9} className="mono" fontSize="8.5" fill="var(--muted)">
        {mx.toFixed(1)}, {my.toFixed(1)} m
      </text>
    </g>
  );
}

function StatusPanel() {
  return (
    <div className="lp-card p-3 shadow-[var(--shadow-lift)] backdrop-blur-sm" aria-label="Simulated scene status">
      <div className="mb-2 flex items-center justify-between">
        <span className="label">Incident status</span>
        <span className="mono text-[9px] uppercase tracking-[0.12em] text-warn">Sim</span>
      </div>
      <dl className="mono space-y-1.5 text-[10.5px]">
        {[
          ["Scene status", "ACTIVE", "text-danger"],
          ["Tracked entities", "03", "text-text"],
          ["Available responders", "02", "text-text"],
          ["Optimization", "READY", "text-accent"],
          ["Command authorization", "REQUIRED", "text-warn"],
        ].map(([k, v, c]) => (
          <div key={k} className="flex items-center justify-between gap-3 border-b border-line/60 pb-1.5 last:border-0 last:pb-0">
            <dt className="uppercase tracking-[0.08em] text-muted">{k}</dt>
            <dd className={`font-medium ${c}`}>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Inspector({ entity }: { entity: Entity | null }) {
  return (
    <div className="lp-card w-full p-3 shadow-[var(--shadow-lift)] backdrop-blur-sm sm:w-[214px]" aria-live="polite">
      <div className="mb-2 flex items-center justify-between">
        <span className="label">Entity inspector</span>
        <span className="mono text-[9px] uppercase tracking-[0.12em] text-warn">Sim</span>
      </div>
      {entity ? (
        <div className="mono space-y-1 text-[10.5px]">
          <p className={`text-[12px] font-medium ${entity.kind === "responder" ? "text-accent" : "text-text"}`}>
            {entity.id} <span className="text-[10px] font-normal uppercase tracking-[0.1em] text-muted">· {entity.kind === "casualty" ? "detected person" : "responder"}</span>
          </p>
          <p className="text-muted">
            pos {toM(entity.x, entity.y)[0].toFixed(1)}, {toM(entity.x, entity.y)[1].toFixed(1)} m
          </p>
          <p className={entity.kind === "casualty" ? "text-text" : "text-ok"}>{entity.status}</p>
          <p className="leading-snug text-muted">{entity.assignment}</p>
        </div>
      ) : (
        <p className="mono text-[10.5px] leading-relaxed text-muted">Hover or focus a marker to inspect it.</p>
      )}
    </div>
  );
}

export function InteractiveHeroScene() {
  const on3D = useEnable3D();
  const [focus, setFocus] = useState<string | null>(null);
  const entity = ENTITIES.find((e) => e.id === focus) ?? null;
  const routeOn = focus !== null && ROUTE_IDS.has(focus);

  // Cursor parallax (motion values: no React re-render per pointer move).
  const px = useMotionValue(0);
  const py = useMotionValue(0);
  const sx = useSpring(px, { stiffness: 90, damping: 20 });
  const sy = useSpring(py, { stiffness: 90, damping: 20 });
  const rotateY = useTransform(sx, [-0.5, 0.5], [-7, 7]);
  const rotateX = useTransform(sy, [-0.5, 0.5], [6, -6]);
  const bgX = useTransform(sx, [-0.5, 0.5], [14, -14]);
  const bgY = useTransform(sy, [-0.5, 0.5], [10, -10]);

  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!on3D || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width - 0.5);
    py.set((e.clientY - r.top) / r.height - 0.5);
  };
  const leave = () => {
    px.set(0);
    py.set(0);
  };

  const layer = (z: number) => (on3D ? { transform: `translateZ(${z}px)` } : undefined);

  return (
    <div className="relative" onPointerMove={move} onPointerLeave={leave} style={on3D ? { perspective: "1400px" } : undefined}>
      <motion.div style={on3D ? { rotateX, rotateY, transformStyle: "preserve-3d" } : undefined} className="relative">
        {/* BACKGROUND: topography, glow, drifting coordinates */}
        <motion.div aria-hidden="true" className="pointer-events-none absolute -inset-10" style={on3D ? { x: bgX, y: bgY, z: -90, scale: 1.08 } : undefined}>
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_55%_50%_at_55%_45%,var(--accent-glow),transparent_70%)]" />
          <svg viewBox="0 0 720 540" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid slice">
            {TOPO.map((d, i) => (
              <path key={i} d={d} fill="none" stroke="var(--accent)" strokeOpacity={0.05 + i * 0.012} strokeWidth="1" />
            ))}
          </svg>
          <div className="lp-drift absolute inset-0">
            {COORDS.map((c) => (
              <span key={c.t} className="mono absolute text-[9.5px] uppercase tracking-[0.18em] text-faint" style={{ left: c.x, top: c.y }}>
                {c.t}
              </span>
            ))}
          </div>
        </motion.div>

        {/* MIDGROUND: the scene map */}
        <div className="lp-corners relative overflow-hidden rounded-[4px] border border-line bg-panel/85 shadow-[var(--scene-shadow)]" style={layer(0)}>
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <div className="mono flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-muted">
              <span className="h-1.5 w-1.5 rounded-full bg-danger blink" aria-hidden="true" />
              Scene · Building A<span className="hidden sm:inline"> · Floor 1</span>
            </div>
            <span className="mono rounded-[2px] border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[9.5px] uppercase tracking-[0.14em] text-warn">Simulated scene</span>
          </div>

          <svg
            viewBox="0 0 720 520"
            className="block h-auto w-full"
            role="group"
            aria-label="Simulated emergency scene: three detected people awaiting assessment, two responders, debris zones, and a proposed route from R-01 to C-001 awaiting command approval. Markers are focusable."
          >
            <defs>
              <pattern id="hs-minor" width="20" height="20" patternUnits="userSpaceOnUse" x="40" y="40">
                <path d="M20 0H0V20" fill="none" stroke="var(--line)" strokeWidth="0.5" />
              </pattern>
              <pattern id="hs-major" width="100" height="100" patternUnits="userSpaceOnUse" x="40" y="80">
                <path d="M100 0H0V100" fill="none" stroke="var(--line-strong)" strokeWidth="0.7" />
              </pattern>
              <pattern id="hs-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <path d="M0 0V6" stroke="var(--danger)" strokeWidth="1.4" strokeOpacity="0.45" />
              </pattern>
              <linearGradient id="hs-fov" x1="1" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="var(--accent)" stopOpacity="0.35" />
                <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
              </linearGradient>
            </defs>

            <rect x="40" y="40" width="640" height="440" fill="url(#hs-minor)" />
            <rect x="40" y="40" width="640" height="440" fill="url(#hs-major)" />
            {[0, 5, 10, 15, 20, 25, 30].map((m) => (
              <text key={`x${m}`} x={40 + m * 20} y="498" className="mono" fontSize="8.5" fill="var(--faint)" textAnchor="middle">
                {m}
              </text>
            ))}
            {[0, 5, 10, 15, 20].map((m) => (
              <text key={`y${m}`} x="30" y={483 - m * 20} className="mono" fontSize="8.5" fill="var(--faint)" textAnchor="end">
                {m}
              </text>
            ))}
            <text x="680" y="512" className="mono" fontSize="8.5" fill="var(--faint)" textAnchor="end">
              x (m)
            </text>

            <polygon points="660,60 300,250 520,470" fill="url(#hs-fov)" className="lp-scan" />

            <g stroke="var(--muted)" strokeWidth="3" fill="none" strokeLinecap="square" opacity="0.75">
              <path d="M60 60 H660 V460 H380 M320 460 H60 V60" />
              <path d="M300 60 V180 M300 240 V300" />
              <path d="M420 300 H500 M560 300 H660" />
            </g>

            <rect x="170" y="320" width="100" height="80" fill="url(#hs-hatch)" stroke="var(--danger)" strokeOpacity="0.5" />
            <text x="175" y="314" className="mono" fontSize="8.5" fill="var(--danger)">
              DEBRIS
            </text>
            <rect x="480" y="100" width="55" height="80" fill="url(#hs-hatch)" stroke="var(--danger)" strokeOpacity="0.5" />
            <text x="482" y="94" className="mono" fontSize="8.5" fill="var(--danger)">
              BLOCKED
            </text>

            {/* proposed route — highlights with R-01 / C-001 */}
            <g
              onPointerEnter={() => setFocus("route")}
              onPointerLeave={() => setFocus(null)}
              style={{ cursor: "pointer" }}
              aria-hidden="true"
            >
              <path d={ROUTE} fill="none" stroke="transparent" strokeWidth="16" />
              <path d={ROUTE} fill="none" stroke="var(--warn)" strokeOpacity={routeOn ? 0.35 : 0.2} strokeWidth={routeOn ? 10 : 6} strokeLinejoin="round" style={{ transition: "all .3s" }} />
              <path d={ROUTE} pathLength={100} fill="none" stroke="var(--warn)" strokeWidth={routeOn ? 2.6 : 1.8} strokeLinejoin="round" className="lp-route-draw" />
              <path
                d={ROUTE}
                pathLength={100}
                fill="none"
                stroke="var(--warn-bright)"
                strokeWidth="3"
                strokeLinecap="round"
                className="lp-packet"
                style={{ animationDuration: routeOn ? "1.4s" : "3s" }}
              />
            </g>
            <g transform="translate(118 300)">
              <rect width={routeOn ? 150 : 92} height="16" rx="2" fill="var(--bg)" stroke="var(--warn)" strokeOpacity="0.6" />
              <text x="6" y="11" className="mono" fontSize="8.5" fill="var(--warn)">
                {routeOn ? "PROPOSED · REV 1 · NEEDS APPROVAL" : "PROPOSED · REV 1"}
              </text>
            </g>

            {ENTITIES.map((e) => (
              <EntityMark key={e.id} e={e} on={focus === e.id || (routeOn && focus === "route" && ROUTE_IDS.has(e.id))} onFocus={setFocus} />
            ))}

            <g transform="translate(660 60)">
              <rect x="-11" y="-8" width="22" height="16" rx="2" fill="var(--panel-2)" stroke="var(--accent)" />
              <circle r="4" fill="none" stroke="var(--accent)" />
              <circle r="1.5" fill="var(--accent)" className="blink" />
            </g>
            <text x="645" y="88" className="mono" fontSize="8.5" fill="var(--accent)" textAnchor="end">
              CAM-01 · PERCEPTION
            </text>
          </svg>
        </div>

        {/* FOREGROUND: floating panels with depth separation */}
        <div
          className="mono absolute left-3 top-12 flex items-center gap-2 rounded-[2px] border border-accent/30 bg-bg/85 px-2 py-1 text-[9.5px] uppercase tracking-[0.12em] text-accent shadow-[var(--shadow-lift)]"
          style={layer(40)}
        >
          <span className="relative flex h-2 w-2" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full rounded-full bg-accent opacity-60 blink" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
          </span>
          Perception · demo playback
        </div>
        <div className="mt-3 grid gap-3 sm:mt-0 sm:block">
          <div className="sm:absolute sm:-left-6 sm:top-[22%] lg:-left-10" style={layer(70)}>
            <Inspector entity={entity} />
          </div>
          <div className="sm:absolute sm:-bottom-6 sm:-right-4 sm:w-[232px] lg:-right-6" style={layer(90)}>
            <StatusPanel />
          </div>
        </div>
      </motion.div>
    </div>
  );
}
