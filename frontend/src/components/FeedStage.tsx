"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { animate, motion, useMotionValue, useReducedMotion } from "framer-motion";
import { reconcileOrder, swapOrder } from "@/lib/feeds";

type Rect = { x: number; y: number; w: number; h: number };

const SWAP_MS = 420;
const EASE = [0.2, 0.7, 0.2, 1] as const;

/**
 * Centralized feed arrangement. `order[0]` is primary; the rest fill the secondary slots.
 * `swapFeed` exchanges the selected feed with the primary one and remembers which two moved (for the animation).
 */
export function useFeedOrder(ids: string[]) {
  const key = ids.join("|");
  // null until the operator swaps something: until then the default arrangement follows the feed list.
  const [raw, setRaw] = useState<string[] | null>(null);
  const [moving, setMoving] = useState<string[]>([]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const order = useMemo(() => (raw ? reconcileOrder(raw, ids) : ids), [raw, key]);
  const orderRef = useRef(order);
  orderRef.current = order;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const swapFeed = useCallback((selectedId: string) => {
    const cur = orderRef.current;
    if (cur.indexOf(selectedId) <= 0) return;
    setMoving([cur[0], selectedId]);
    setRaw(swapOrder(cur, selectedId));
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMoving([]), SWAP_MS + 80);
  }, []);
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  return { order, primaryId: order[0], secondaryIds: order.slice(1), swapFeed, moving };
}

/** Measures slot placeholders relative to the stage so feeds can be positioned over them without re-parenting. */
export function useSlotRects(stageRef: RefObject<HTMLElement | null>, count: number, enabled: boolean) {
  const els = useRef<(HTMLElement | null)[]>([]);
  const [rects, setRects] = useState<(Rect | null)[]>([]);
  const refs = useMemo(() => Array.from({ length: count }, (_, i) => (el: HTMLElement | null) => void (els.current[i] = el)), [count]);

  const measure = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const s = stage.getBoundingClientRect();
    const next = els.current.slice(0, count).map((el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left - s.left, y: r.top - s.top, w: r.width, h: r.height };
    });
    setRects((prev) =>
      prev.length === next.length &&
      prev.every((p, i) => {
        const n = next[i];
        return p && n ? p.x === n.x && p.y === n.y && p.w === n.w && p.h === n.h : p === n;
      })
        ? prev
        : next,
    );
  }, [stageRef, count]);

  useLayoutEffect(() => {
    if (!enabled) return;
    measure();
    const ro = new ResizeObserver(measure);
    if (stageRef.current) ro.observe(stageRef.current);
    els.current.slice(0, count).forEach((el) => el && ro.observe(el));
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [enabled, measure, count, stageRef]);

  return { slotRefs: refs, rects };
}

/**
 * One feed, positioned over its current slot. The element (and everything inside it — video
 * elements, media streams, map state) stays mounted across swaps; only position/size animate.
 */
export function FeedFrame({
  id,
  name,
  statusText,
  rect,
  primary,
  moving,
  onExpand,
  className = "",
  children,
}: {
  id: string;
  name: string;
  statusText: string;
  rect: Rect | null | undefined;
  primary: boolean;
  moving: boolean;
  onExpand: (id: string) => void;
  className?: string;
  children: ReactNode;
}) {
  const reduced = useReducedMotion();
  const animateSwap = moving && !reduced;
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const width = useMotionValue(0);
  const height = useMotionValue(0);
  const opacity = useMotionValue(1);

  // Layout/resize updates snap; only a swap animates (size, position, and a slight opacity dip).
  useLayoutEffect(() => {
    if (!rect) return;
    const pairs = [
      [x, rect.x],
      [y, rect.y],
      [width, rect.w],
      [height, rect.h],
    ] as const;
    if (!animateSwap) {
      pairs.forEach(([mv, v]) => mv.set(v));
      opacity.set(1);
      return;
    }
    const opts = { duration: SWAP_MS / 1000, ease: EASE };
    const controls = [...pairs.map(([mv, v]) => animate(mv, v, opts)), animate(opacity, [1, 0.82, 1], opts)];
    return () => controls.forEach((c) => c.stop());
  }, [rect, animateSwap, x, y, width, height, opacity]);

  return (
    <motion.div
      data-feed-id={id}
      data-feed-slot={primary ? "primary" : "secondary"}
      className="absolute left-0 top-0"
      style={{ x, y, width, height, opacity, zIndex: moving ? (primary ? 3 : 2) : 1, visibility: rect ? "visible" : "hidden" }}
    >
      {/* Same element in both positions so children never remount; only props change. */}
      <div
        role={primary ? "region" : "button"}
        tabIndex={primary ? undefined : 0}
        aria-label={primary ? `Primary view: ${name} (${statusText})` : `${name} — ${statusText}. Expand to primary view`}
        title={primary ? undefined : `${name} · ${statusText} · click to expand`}
        onClick={primary ? undefined : () => onExpand(id)}
        onKeyDown={
          primary
            ? undefined
            : (e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onExpand(id);
                }
              }
        }
        className={`group/tile relative h-full w-full rounded outline-none ${
          primary ? "" : "cursor-pointer transition-shadow hover:ring-1 hover:ring-accent/70 focus-visible:ring-2 focus-visible:ring-accent"
        } ${className}`}
      >
        {children}
        {!primary && (
          <span
            aria-hidden="true"
            className="mono pointer-events-none absolute right-1 top-1 z-20 rounded-[2px] bg-accent px-1 py-px text-[9px] font-semibold tracking-[0.12em] text-on-accent opacity-0 transition-opacity group-hover/tile:opacity-100 group-focus-visible/tile:opacity-100"
          >
            ⤢ EXPAND
          </span>
        )}
      </div>
    </motion.div>
  );
}
