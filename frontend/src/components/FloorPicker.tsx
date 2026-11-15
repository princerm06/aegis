"use client";

import { useEffect, useRef, useState } from "react";
import { FLOORS, floorById, type FloorSummary } from "@/lib/floors";

/**
 * Compact floor selector pinned to the primary viewport's top-left. The closed button shows the
 * floor on screen and a mini stack of every floor (lit where people are detected); the open
 * panel is a grid of floor buttons with counts. Choosing a floor shows it on the main panel.
 */
export function FloorPicker({
  floor,
  summaries,
  preview,
  onSelect,
}: {
  floor: string;
  summaries: Record<string, FloorSummary>;
  preview: boolean;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const cur = floorById(floor);
  const stack = [...FLOORS].reverse(); // top floor first, like an elevator panel
  const occupied = FLOORS.filter((f) => people(summaries[f.id]) > 0).length;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative" data-testid="floor-picker">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={`Floor: ${cur.short} ${cur.name}. People detected on ${occupied} floor${occupied === 1 ? "" : "s"}. Change floor`}
        title="Change floor (people detected per floor)"
        className="mono flex items-center gap-1.5 rounded border border-line-strong bg-panel/95 px-1.5 py-1 text-[10.5px] shadow-md backdrop-blur hover:border-accent"
      >
        {/* mini building: one bar per floor, lit when people are detected there */}
        <span className="flex flex-col gap-px" aria-hidden="true">
          {stack.map((f) => {
            const s = summaries[f.id];
            const n = people(s);
            return (
              <span
                key={f.id}
                className={`block h-[3px] w-3.5 rounded-[1px] ${
                  f.id === floor ? "outline outline-1 outline-text" : ""
                } ${n ? (s.attention ? "bg-warn" : "bg-accent") : "bg-line-strong"}`}
              />
            );
          })}
        </span>
        <span className="font-semibold text-text">{cur.short}</span>
        <span className="text-muted">{cur.name}</span>
        <span className="text-faint">▾</span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Floors"
          className="absolute left-0 top-full z-10 mt-1 w-56 rounded-md border border-line-strong bg-panel p-1.5 shadow-2xl shadow-black/40"
        >
          <div className="label mb-1 flex items-center justify-between px-0.5">
            <span>Floors · people detected</span>
            {preview && <span className="text-warn">PREVIEW</span>}
          </div>
          <div className="grid grid-cols-1 gap-1">
            {stack.map((f) => {
              const s = summaries[f.id];
              const n = people(s);
              const active = f.id === floor;
              return (
                <button
                  key={f.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  data-floor={f.id}
                  data-people={n}
                  onClick={() => {
                    onSelect(f.id);
                    setOpen(false);
                  }}
                  className={`mono grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-1.5 rounded border px-1.5 py-1 text-left text-[10.5px] ${
                    active ? "border-accent bg-accent/10" : "border-line hover:border-line-strong"
                  }`}
                >
                  <span className={`text-center font-bold ${active ? "text-accent" : "text-text"}`}>{f.short}</span>
                  <span className="min-w-0">
                    <span className="block truncate text-text">{f.name}</span>
                    <span className="block truncate text-[9.5px] text-faint">
                      {f.outline ? "" : "no plan · "}
                      {s.responders ? `${s.responders} FF` : "no FF"}
                    </span>
                  </span>
                  <span
                    className={`flex items-center gap-1 rounded-[2px] px-1 py-px text-[10px] font-semibold ${
                      n ? (s.attention ? "bg-warn text-on-warn" : "bg-accent text-on-accent") : "text-faint"
                    }`}
                    title={n ? `${s.victims} victim${s.victims === 1 ? "" : "s"}, ${s.detections} unconfirmed detection${s.detections === 1 ? "" : "s"}` : "No one detected"}
                  >
                    {n ? `● ${n}` : "—"}
                  </span>
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 px-0.5 text-[9.5px] leading-snug text-faint">
            {preview
              ? "Floor assignments are mock PREVIEW data. Routing grid is Level 1 only."
              : "Backend has no floor data yet: everyone is shown on L1, where the routing grid lives."}
          </p>
        </div>
      )}
    </div>
  );
}

const people = (s: FloorSummary | undefined) => (s ? s.victims + s.detections : 0);
