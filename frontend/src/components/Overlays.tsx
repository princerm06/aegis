"use client";

import { useRef, useState } from "react";
import type { Alert } from "@/lib/useAlerts";
import type { PreviewFlags } from "@/lib/preview";

const LEVEL: Record<Alert["level"], string> = {
  info: "border-info/60 bg-tone-info",
  ok: "border-ok/60 bg-tone-ok",
  warn: "border-warn/70 bg-tone-warn",
  danger: "border-danger bg-tone-danger",
};
const ICON: Record<Alert["level"], string> = { info: "ℹ", ok: "✓", warn: "!", danger: "⚠" };

export function AlertStack({ alerts, onDismiss }: { alerts: Alert[]; onDismiss: (k: string) => void }) {
  return (
    <div className="pointer-events-none fixed right-3 top-14 z-50 flex w-80 flex-col gap-1.5" aria-live="assertive">
      {alerts.map((a) => (
        <div key={a.key} className={`pointer-events-auto flex items-start gap-2 rounded border px-2.5 py-1.5 shadow-lg shadow-black/50 ${LEVEL[a.level]} ${a.level === "danger" ? "flash" : ""}`}>
          <span className="mono mt-px text-[12px] font-bold">{ICON[a.level]}</span>
          <div className="min-w-0 flex-1">
            <p className="text-[12px] font-semibold">{a.title}</p>
            {a.detail && <p className="truncate text-[11px] text-muted">{a.detail}</p>}
          </div>
          <button onClick={() => onDismiss(a.key)} className="text-muted hover:text-text" aria-label="Dismiss">
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

interface MenuProps {
  flags: PreviewFlags;
  onFlags: (p: Partial<PreviewFlags>) => void;
  fallback: boolean;
  onFallback: (v: boolean) => void;
  floorPlan: { url: string; opacity: number } | null;
  onFloorPlan: (v: { url: string; opacity: number } | null) => void;
  onExport: () => void;
  onHelp: () => void;
}

export function SettingsMenu({ flags, onFlags, fallback, onFallback, floorPlan, onFloorPlan, onExport, onHelp }: MenuProps) {
  const [open, setOpen] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const row = "flex items-center justify-between gap-3 py-1";
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="rounded border border-line-strong px-2 py-1 text-[11px] hover:border-muted" aria-expanded={open}>
        ☰ Setup
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-50 mt-1 w-80 rounded-md border border-line-strong bg-panel p-3 text-[11.5px] shadow-2xl shadow-black/60">
            <p className="label mb-1">Preview data (mock, labelled)</p>
            <label className={row}>
              <span>
                Sensor heart rate <span className="text-faint">· no backend yet</span>
              </span>
              <input type="checkbox" checked={flags.sensorVitals} onChange={(e) => onFlags({ sensorVitals: e.target.checked })} />
            </label>
            <label className={row}>
              <span>
                Device online status <span className="text-faint">· no backend yet</span>
              </span>
              <input type="checkbox" checked={flags.deviceStatus} onChange={(e) => onFlags({ deviceStatus: e.target.checked })} />
            </label>

            <p className="label mb-1 mt-3">Reliability fallback</p>
            <label className={row}>
              <span>Show seeded scenario (read-only, not live)</span>
              <input type="checkbox" checked={fallback} onChange={(e) => onFallback(e.target.checked)} />
            </label>
            <p className="text-[10.5px] text-faint">For recorded footage, set a feed&apos;s source to a video URL (e.g. /fallback/bodycam.mp4 in frontend/public).</p>

            <p className="label mb-1 mt-3">Floor plan under map</p>
            <div className="flex items-center gap-2">
              <button onClick={() => file.current?.click()} className="rounded border border-line-strong px-2 py-0.5 hover:border-accent">
                {floorPlan ? "Replace image" : "Upload image"}
              </button>
              {floorPlan && (
                <>
                  <input
                    type="range"
                    min={0.1}
                    max={1}
                    step={0.05}
                    value={floorPlan.opacity}
                    onChange={(e) => onFloorPlan({ ...floorPlan, opacity: Number(e.target.value) })}
                    className="flex-1"
                    aria-label="Floor plan opacity"
                  />
                  <button onClick={() => onFloorPlan(null)} className="text-muted hover:text-text">
                    clear
                  </button>
                </>
              )}
              <input
                ref={file}
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  const reader = new FileReader();
                  reader.onload = () => onFloorPlan({ url: String(reader.result), opacity: floorPlan?.opacity ?? 0.5 });
                  reader.readAsDataURL(f);
                  e.target.value = "";
                }}
              />
            </div>
            <p className="text-[10.5px] text-faint">Stretched to the map bounds; align it to the taped floor.</p>

            <div className="mt-3 flex gap-2 border-t border-line pt-2">
              <button onClick={onExport} className="rounded border border-line-strong px-2 py-1 hover:border-accent">
                ⇩ Export incident summary
              </button>
              <button onClick={onHelp} className="rounded border border-line-strong px-2 py-1 hover:border-accent">
                Shortcuts (?)
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export const SHORTCUTS: [string, string][] = [
  ["A", "Approve current recommendation (exact revision)"],
  ["D", "Dispatch the approved recommendation"],
  ["1 – 9", "Select firefighter (bodycam + comms)"],
  ["Space (hold)", "Talk to selected firefighter"],
  ["B", "Toggle block-route mode"],
  ["M", "Mark selected firefighter's position on the map"],
  ["L", "Open timeline & details"],
  ["Esc", "Cancel mode / close panels"],
  ["?", "This help"],
];

export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="w-96 rounded-md border border-line-strong bg-panel p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center justify-between">
          <span className="label !text-text">Keyboard shortcuts</span>
          <button onClick={onClose} className="text-muted hover:text-text" aria-label="Close">
            ✕
          </button>
        </div>
        <table className="w-full text-[12px]">
          <tbody>
            {SHORTCUTS.map(([k, v]) => (
              <tr key={k}>
                <td className="py-1 pr-3">
                  <kbd className="mono rounded border border-line-strong bg-bg px-1.5 py-0.5 text-[11px]">{k}</kbd>
                </td>
                <td className="py-1 text-muted">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[10.5px] text-faint">Shortcuts are ignored while typing. Approve and dispatch remain separate explicit actions.</p>
      </div>
    </div>
  );
}
