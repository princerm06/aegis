"use client";

import { useEffect, useState } from "react";

type Status = { kind: "checking" } | { kind: "online"; sequence: number } | { kind: "offline" };

/** Real reachability of the local simulation backend — never mocked. */
export function BackendStatus() {
  const [status, setStatus] = useState<Status>({ kind: "checking" });

  useEffect(() => {
    let alive = true;
    const check = async () => {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 3000);
      try {
        const res = await fetch("/aegis-api/api/v1/simulations/demo/state", { signal: ctrl.signal, cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const body = (await res.json()) as { sequence?: number };
        if (alive) setStatus({ kind: "online", sequence: body.sequence ?? 0 });
      } catch {
        if (alive) setStatus({ kind: "offline" });
      } finally {
        clearTimeout(t);
      }
    };
    check();
    const id = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const color = status.kind === "online" ? "bg-ok" : status.kind === "offline" ? "bg-faint" : "bg-warn";
  const text =
    status.kind === "online"
      ? `Simulation backend online · state seq ${status.sequence}`
      : status.kind === "offline"
        ? "Simulation backend not running · start it to use the command center"
        : "Checking simulation backend…";

  return (
    <p className="mono flex items-center gap-2 text-[11px] text-muted" role="status" aria-live="polite">
      <span className={`h-1.5 w-1.5 rounded-full ${color}`} aria-hidden="true" />
      {text}
    </p>
  );
}
