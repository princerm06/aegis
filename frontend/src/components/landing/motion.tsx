"use client";

import { useEffect, useRef, useState, type ElementType, type PointerEvent, type ReactNode } from "react";
import { motion, useReducedMotion, useScroll, useTransform, type MotionValue } from "framer-motion";

export function useMediaQuery(query: string) {
  const [match, setMatch] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** 3D is used only on tablet+ with motion allowed; everything else gets flat layouts. */
export function useEnable3D() {
  const reduced = useReducedMotion();
  const wide = useMediaQuery("(min-width: 768px)");
  return wide && !reduced;
}

const DEPTH = 150; // px pushed back at the edge of a transition
const TILT_IN = 6; // deg
const TILT_OUT = -4;
const SHRINK = 0.045;

/**
 * Scroll-linked spatial transition for a section.
 * Entering: rises from depth (hinged at its top edge). Leaving: recedes (hinged at its bottom edge).
 * Scroll-driven both ways; the middle of a long section stays at rest so it reads normally.
 */
export function SectionTransition({
  children,
  enter = true,
  leave = true,
  className = "",
}: {
  children: ReactNode;
  enter?: boolean;
  leave?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const on = useEnable3D();
  const { scrollYProgress: inP } = useScroll({ target: ref, offset: ["start end", "start 0.45"] });
  const { scrollYProgress: outP } = useScroll({ target: ref, offset: ["end 0.55", "end start"] });

  const pair: MotionValue<number>[] = [inP, outP];
  const e = (v: number[]) => (enter ? 1 - v[0] : 0);
  const l = (v: number[]) => (leave ? v[1] : 0);
  const z = useTransform(pair, (v: number[]) => -DEPTH * (e(v) + l(v)));
  const rotateX = useTransform(pair, (v: number[]) => TILT_IN * e(v) + TILT_OUT * l(v));
  const scale = useTransform(pair, (v: number[]) => 1 - SHRINK * (e(v) + l(v)));
  const opacity = useTransform(pair, (v: number[]) => Math.min(1 - 0.6 * e(v), 1 - 0.55 * l(v)));
  const origin = useTransform(pair, (v: number[]) => (l(v) > 0 && e(v) === 0 ? "50% 100%" : "50% 0%"));

  if (!on) return <div ref={ref} className={className}>{children}</div>;
  return (
    <div ref={ref} className={className} style={{ perspective: "1600px" }}>
      <motion.div style={{ z, rotateX, scale, opacity, transformOrigin: origin, perspectiveOrigin: origin }}>{children}</motion.div>
    </div>
  );
}

/** Background coordinates + crosshairs that move slower than the content (parallax). */
export function ParallaxDecor({ seed = 0 }: { seed?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const on = useEnable3D();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] });
  const yFar = useTransform(scrollYProgress, [0, 1], [26, -26]);
  const yNear = useTransform(scrollYProgress, [0, 1], [60, -60]);
  // Labels sit in the section's empty top/bottom padding so they never collide with content.
  const labels = [
    { style: { right: "6%", top: 34 }, t: `x ${(12.4 + seed * 3.1).toFixed(1)} · y ${(4.2 + seed).toFixed(1)} m` },
    { style: { left: "4%", bottom: 30 }, t: `GRID ${String(seed + 1).padStart(2, "0")} · 20 px/m` },
  ];
  return (
    <div ref={ref} className="pointer-events-none absolute inset-0 -z-10 overflow-hidden" aria-hidden="true">
      <motion.div style={on ? { y: yFar } : undefined} className="absolute inset-0">
        {labels.map((l) => (
          <span key={l.t} className="mono lp-drift absolute text-[10px] uppercase tracking-[0.18em] text-faint/70" style={l.style}>
            {l.t}
          </span>
        ))}
      </motion.div>
      <motion.div style={on ? { y: yNear } : undefined} className="absolute inset-0">
        {[
          { right: "3%", top: "38%" },
          { left: "2.5%", top: "70%" },
        ].map((pos, i) => (
          <svg key={i} width="22" height="22" viewBox="0 0 22 22" className="absolute text-line-strong" style={pos}>
            <path d="M11 0v8M11 14v8M0 11h8M14 11h8" stroke="currentColor" />
          </svg>
        ))}
      </motion.div>
    </div>
  );
}

/** Heading/content reveal: a short upward settle when it first enters the viewport. */
export function Reveal({ children, delay = 0, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -12% 0px" }}
      transition={{ duration: 0.6, ease: [0.2, 0.7, 0.2, 1], delay }}
    >
      {children}
    </motion.div>
  );
}

/** Card that lifts on hover and tilts very slightly toward the pointer (fine pointers only). */
export function MotionCard({
  children,
  className = "",
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: ElementType;
}) {
  const ref = useRef<HTMLElement>(null);
  const move = (e: PointerEvent<HTMLElement>) => {
    if (e.pointerType !== "mouse" || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    ref.current.style.setProperty("--ry", `${(px * 4).toFixed(2)}deg`);
    ref.current.style.setProperty("--rx", `${(-py * 4).toFixed(2)}deg`);
  };
  const reset = () => {
    ref.current?.style.setProperty("--ry", "0deg");
    ref.current?.style.setProperty("--rx", "0deg");
  };
  return (
    <Tag ref={ref} onPointerMove={move} onPointerLeave={reset} className={`lp-motion-card ${className}`}>
      {children}
    </Tag>
  );
}
