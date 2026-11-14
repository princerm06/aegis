"use client";

import { useEffect, useState } from "react";
import { motion, useReducedMotion, useScroll, useSpring, useTransform, useVelocity } from "framer-motion";

export const SECTIONS = [
  { id: "top", label: "Overview" },
  { id: "platform", label: "Problem" },
  { id: "how-it-works", label: "Workflow" },
  { id: "technology", label: "Technology" },
  { id: "human-in-the-loop", label: "Human Control" },
  { id: "hardware", label: "Hardware" },
] as const;

const num = (i: number) => String(i + 1).padStart(2, "0");

/** Active section from real scroll position: the last section whose top has crossed 40% of the viewport. */
function useActiveSection() {
  const [active, setActive] = useState(0);
  const [pastHero, setPastHero] = useState(false);
  useEffect(() => {
    let raf = 0;
    const compute = () => {
      raf = 0;
      const line = window.innerHeight * 0.4;
      let idx = 0;
      SECTIONS.forEach((s, i) => {
        const el = document.getElementById(s.id);
        if (el && el.getBoundingClientRect().top <= line) idx = i;
      });
      setActive(idx);
      setPastHero(window.scrollY > window.innerHeight * 0.55);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(compute);
    };
    compute();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  return { active, pastHero };
}

const ITEM = 40; // px per indicator row

export function FloatingSectionNavigation() {
  const { active, pastHero } = useActiveSection();
  const reduced = useReducedMotion();

  // The rail leans with scroll velocity and dips back in depth while the page is moving.
  const { scrollY, scrollYProgress } = useScroll();
  const velocity = useSpring(useVelocity(scrollY), { stiffness: 140, damping: 32 });
  const tilt = useTransform(velocity, [-2600, 0, 2600], [7, 0, -7], { clamp: true });
  const depth = useTransform(velocity, [-2600, 0, 2600], [-36, 0, -36], { clamp: true });
  const spring = reduced ? { duration: 0 } : { type: "spring" as const, stiffness: 300, damping: 30 };

  return (
    <>
      {/* Desktop rail */}
      <nav aria-label="Page sections" className="fixed right-5 top-1/2 z-40 hidden -translate-y-1/2 min-[1360px]:block" style={{ perspective: 700 }}>
        <motion.div
          style={reduced ? undefined : { rotateY: -16, rotateX: tilt, z: depth, transformStyle: "preserve-3d" }}
          className="relative rounded-[5px] border border-line bg-bg/75 p-1.5 shadow-[var(--shadow-lift)] backdrop-blur-md"
        >
          {/* progress track */}
          <span className="absolute bottom-3 left-[3px] top-3 w-px bg-line" aria-hidden="true">
            <motion.span className="block h-full w-px origin-top bg-accent/70" style={{ scaleY: scrollYProgress }} />
          </span>
          {/* active indicator */}
          <motion.span
            aria-hidden="true"
            className="lp-glow absolute left-1.5 right-1.5 top-1.5 rounded-[3px] bg-accent/15"
            style={{ height: ITEM }}
            animate={{ y: active * ITEM }}
            transition={spring}
          />
          <ul className="relative">
            {SECTIONS.map((s, i) => {
              const on = i === active;
              return (
                <li key={s.id}>
                  <a
                    href={`#${s.id}`}
                    aria-current={on ? "true" : undefined}
                    aria-label={`${num(i)} ${s.label}`}
                    className="group relative flex h-10 w-10 items-center justify-center rounded-[3px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <span className={`mono text-[11px] transition-colors ${on ? "font-semibold text-accent" : "text-faint group-hover:text-text"}`}>{num(i)}</span>
                    <span
                      className="mono pointer-events-none absolute right-full mr-3 translate-x-2 whitespace-nowrap rounded-[3px] border border-line bg-panel px-2.5 py-1 text-[10.5px] uppercase tracking-[0.14em] text-text opacity-0 shadow-[var(--shadow-lift)] transition-all duration-300 group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:translate-x-0 group-focus-visible:opacity-100"
                    >
                      <span className="text-accent">{num(i)}</span> — {s.label}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        </motion.div>
      </nav>

      {/* Compact bottom tracker (mobile, tablet, small laptops) */}
      <nav
        aria-label="Page sections"
        aria-hidden={!pastHero}
        className={`pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-4 pb-[calc(10px+env(safe-area-inset-bottom))] transition-all duration-300 min-[1360px]:hidden ${
          pastHero ? "translate-y-0 opacity-100" : "translate-y-4 opacity-0"
        }`}
      >
        <div className={`flex items-center gap-3 rounded-full border border-line bg-bg/90 py-1 pl-3.5 pr-2 shadow-[var(--shadow-lift)] backdrop-blur-md ${pastHero ? "pointer-events-auto" : ""}`}>
          <span className="mono w-[108px] truncate text-[10.5px] uppercase tracking-[0.12em] text-text" aria-live="polite">
            <span className="text-accent">{num(active)}</span> {SECTIONS[active].label}
          </span>
          <ul className="flex items-center">
            {SECTIONS.map((s, i) => (
              <li key={s.id}>
                <a href={`#${s.id}`} tabIndex={pastHero ? 0 : -1} aria-label={`${num(i)} ${s.label}`} aria-current={i === active ? "true" : undefined} className="flex h-8 items-center px-[3px]">
                  <span
                    className={`block h-1.5 rounded-full transition-all duration-300 ${i === active ? "w-5 bg-accent" : i < active ? "w-1.5 bg-accent/50" : "w-1.5 bg-line-strong"}`}
                  />
                </a>
              </li>
            ))}
          </ul>
        </div>
      </nav>
    </>
  );
}
