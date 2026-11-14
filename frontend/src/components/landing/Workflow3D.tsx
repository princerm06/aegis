"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { AnimatePresence, motion, useMotionValue, useReducedMotion } from "framer-motion";
import { Eye, Map, Network, ShieldCheck, Send, RefreshCw, ChevronDown } from "lucide-react";
import { Section, SectionHeading } from "./Section";
import { useMediaQuery } from "./motion";

const STAGES = [
  {
    key: "observe",
    title: "Observe",
    caption: "detect + track",
    icon: Eye,
    short: "Computer vision detects and tracks people through a webcam using YOLO and ByteTrack.",
    detail:
      "Each frame from the webcam passes through YOLO person detection. ByteTrack keeps a persistent track ID for each person across frames, so the system follows people rather than re-detecting strangers.",
    input: "Webcam frames",
    output: "Person tracks with pixel boxes",
    note: "Camera output is evidence for the operator, never a medical assessment.",
    tech: ["Logitech webcam", "YOLO", "ByteTrack"],
  },
  {
    key: "map",
    title: "Map",
    caption: "pixels → meters",
    icon: Map,
    short: "OpenCV homography maps detected positions into a shared 2.5D scene representation.",
    detail:
      "Calibration points define a homography from the camera image to the floor plane. Each track's foot point is projected into meters on a shared scene map with an occupancy grid for walls and debris.",
    input: "Pixel tracks + calibration",
    output: "Positions in scene meters",
    note: "The operator links tracks to casualties — association is a human-confirmed step.",
    tech: ["OpenCV", "Homography", "Occupancy grid"],
  },
  {
    key: "optimize",
    title: "Optimize",
    caption: "assign + route",
    icon: Network,
    short: "Google OR-Tools recommends feasible responder assignments. A* calculates obstacle-aware routes.",
    detail:
      "OR-Tools matches available responders to casualties using human-confirmed priorities. A* searches the occupancy grid for routes around obstacles. The result is a revisioned recommendation that expires if not acted on.",
    input: "Confirmed priorities, availability, map",
    output: "Recommendation rev N with routes",
    note: "Only human-entered priorities feed the optimizer.",
    tech: ["Google OR-Tools", "A* pathfinding", "FastAPI"],
  },
  {
    key: "approve",
    title: "Approve",
    caption: "human decision",
    icon: ShieldCheck,
    short: "Incident commanders review recommendations and explicitly approve assignments.",
    detail:
      "The commander approves one exact revision of the recommendation. If anything changes — a new casualty, a blocked cell, a responder going offline — that approval is invalidated and a new revision must be reviewed.",
    input: "Recommendation rev N",
    output: "Approval bound to rev N",
    note: "Nothing is dispatched automatically.",
    tech: ["Command dashboard", "Idempotent decisions"],
  },
  {
    key: "dispatch",
    title: "Dispatch",
    caption: "task to device",
    icon: Send,
    short: "Approved tasks are delivered to physical responder hardware through an Arduino-based interface.",
    detail:
      "Dispatch is a separate, explicit action after approval. Each assignment becomes a task command addressed to the responder's device, which lights an LED to signal an incoming task.",
    input: "Approved revision",
    output: "Task commands to responder devices",
    note: "Approval and dispatch are deliberately two steps.",
    tech: ["Arduino UNO R4 WiFi", "Serial", "Grove LED"],
  },
  {
    key: "adapt",
    title: "Adapt",
    caption: "acknowledge + replan",
    icon: RefreshCw,
    short: "Physical acknowledgments update system state. Changes in the scene trigger dynamic replanning.",
    detail:
      "A button press on the responder device records delivery and acknowledgment. New casualties, unavailable responders, and blocked routes produce a fresh recommendation revision — back to command for review.",
    input: "Acknowledgments + scene changes",
    output: "Updated state, new revision",
    note: "The loop closes only when a responder physically confirms.",
    tech: ["Grove button", "WebSockets", "Replanning"],
  },
];
const N = STAGES.length;
const CHAIN = ["Observe", "Map", "Optimize", "Approve", "Dispatch", "Acknowledge", "Adapt"];
const xOf = (i: number) => ((i + 0.5) / N) * 100;

function StageDetail({ i }: { i: number }) {
  const s = STAGES[i];
  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
      <div>
        <p className="mono text-[12px] uppercase tracking-[0.16em] text-accent">
          Stage {String(i + 1).padStart(2, "0")} · {s.title}
        </p>
        <p className="mt-3 text-[20px] font-medium leading-snug tracking-[-0.01em] text-text sm:text-[22px]">{s.short}</p>
        <p className="mt-4 text-[15px] leading-relaxed text-muted">{s.detail}</p>
        <p className="mt-5 border-l-2 border-warn/60 pl-3 text-[13.5px] text-text/90">{s.note}</p>
      </div>
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 rounded-[3px] border border-line bg-bg/50 p-4">
          <span className="label self-center">In</span>
          <span className="mono text-[13px] text-text">{s.input}</span>
          <span className="label self-center">Out</span>
          <span className="mono text-[13px] text-accent">{s.output}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {s.tech.map((t) => (
            <span key={t} className="mono rounded-[2px] border border-line-strong px-2 py-1 text-[11.5px] text-muted">
              {t}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export function Workflow3D() {
  const reduced = useReducedMotion();
  const desktop = useMediaQuery("(min-width: 1024px)");
  const pinned = useMediaQuery("(min-width: 1024px) and (min-height: 820px)") && !reduced;
  const [active, setActive] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Pinned mode: scroll position drives the active stage (both directions).
  // Measured live from the track's rect so it stays correct when the track height changes.
  const scrollYProgress = useMotionValue(0);
  useEffect(() => {
    if (!pinned) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const el = trackRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const range = r.height - window.innerHeight;
      const v = range > 0 ? Math.min(1, Math.max(0, -r.top / range)) : 0;
      scrollYProgress.set(v);
      const i = Math.min(N - 1, Math.floor(v * N));
      setActive((a) => (a === i ? a : i));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [pinned, scrollYProgress]);

  const select = (i: number) => {
    const el = trackRef.current;
    if (pinned && el) {
      // Keep scroll and selection in sync: jump to the scroll position of that stage.
      const top = el.getBoundingClientRect().top + window.scrollY;
      const range = el.offsetHeight - window.innerHeight;
      window.scrollTo({ top: top + range * ((i + 0.5) / N), behavior: reduced ? "auto" : "smooth" });
    }
    setActive(i);
  };

  const onKey = (e: KeyboardEvent, i: number) => {
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % N;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + N) % N;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = N - 1;
    if (next >= 0) {
      e.preventDefault();
      select(next);
      tabRefs.current[next]?.focus();
    }
  };

  const reach = hover ?? active;
  const spring = reduced ? { duration: 0 } : { type: "spring" as const, stiffness: 220, damping: 26 };

  const heading = (
    <div className="flex flex-col gap-5">
      <SectionHeading
        index="02"
        eyebrow="The AEGIS solution"
        title="One system. A complete response loop."
        lede={pinned ? undefined : "Six stages connect what the camera sees to a responder physically confirming a task — with a human decision in the middle."}
      />
      <p className="mono flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] uppercase tracking-[0.14em] text-faint" aria-label="Full loop: observe, map, optimize, approve, dispatch, acknowledge, adapt">
        {CHAIN.map((c, i) => (
          <span key={c} className="flex items-center gap-2">
            <span className={c === "Acknowledge" ? "text-warn" : c === "Approve" ? "text-text" : ""}>{c}</span>
            {i < CHAIN.length - 1 && <span aria-hidden="true">→</span>}
          </span>
        ))}
      </p>
    </div>
  );

  // ── Desktop: spatial node path ──
  const desktopNodes = (
    <div className="relative mt-12" style={{ perspective: "1200px" }}>
      <svg className="pointer-events-none absolute inset-x-0 top-[34px] h-4 w-full" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true">
        <line x1={xOf(0)} x2={xOf(N - 1)} y1="2" y2="2" stroke="var(--line-strong)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <line x1={xOf(0)} x2={xOf(active)} y1="2" y2="2" stroke="var(--accent)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" style={{ transition: "all .5s" }} />
        {hover !== null && hover !== active && (
          <line
            x1={xOf(Math.min(hover, active))}
            x2={xOf(Math.max(hover, active))}
            y1="2"
            y2="2"
            stroke="var(--warn)"
            strokeWidth="2"
            strokeDasharray="1 1"
            vectorEffect="non-scaling-stroke"
          />
        )}
        <line x1={xOf(0)} x2={xOf(N - 1)} y1="2" y2="2" pathLength={100} stroke="var(--accent)" strokeWidth="3" vectorEffect="non-scaling-stroke" className="lp-packet-slow" />
      </svg>

      <div role="tablist" aria-label="Response loop stages" className="relative grid grid-cols-6" style={{ transformStyle: "preserve-3d" }}>
        {STAGES.map((st, i) => {
          const on = i === active;
          const lit = i <= reach;
          const dist = Math.abs(i - active);
          const Icon = st.icon;
          return (
            <motion.button
              key={st.key}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              role="tab"
              id={`stage-tab-${st.key}`}
              aria-selected={on}
              aria-controls="stage-panel"
              tabIndex={on ? 0 : -1}
              onClick={() => select(i)}
              onKeyDown={(e) => onKey(e, i)}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
              animate={{
                z: on ? 60 : -Math.min(dist, 3) * 22,
                rotateY: Math.max(-18, Math.min(18, (active - i) * 6)),
                opacity: on ? 1 : Math.max(0.55, 1 - dist * 0.13),
              }}
              transition={spring}
              className="group flex flex-col items-center gap-3 rounded-[4px] px-2 pb-3 pt-2 text-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              <span
                className={`relative flex h-[54px] w-[54px] items-center justify-center rounded-full border bg-bg transition-all duration-300 ${
                  on ? "lp-glow border-accent text-accent" : lit ? "border-accent/60 text-accent/80" : "border-line-strong text-muted group-hover:border-accent/50 group-hover:text-text"
                }`}
              >
                <Icon size={20} aria-hidden="true" className="lp-icon group-hover:-translate-y-0.5" />
              </span>
              <span>
                <span className={`mono block text-[11px] ${on ? "text-accent" : "text-faint"}`}>{String(i + 1).padStart(2, "0")}</span>
                <span className={`mono block text-[13px] font-medium uppercase tracking-[0.16em] ${on ? "text-text" : "text-muted"}`}>{st.title}</span>
                <span className="mono mt-0.5 block text-[10px] uppercase tracking-[0.1em] text-faint">{st.caption}</span>
              </span>
            </motion.button>
          );
        })}
      </div>

      <div id="stage-panel" role="tabpanel" aria-labelledby={`stage-tab-${STAGES[active].key}`} className="relative mt-8" style={{ perspective: "1400px" }}>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={active}
            initial={{ opacity: 0, z: -80, rotateX: 4 }}
            animate={{ opacity: 1, z: 0, rotateX: 0 }}
            exit={{ opacity: 0, z: -60, rotateX: -3 }}
            transition={{ duration: reduced ? 0 : 0.32, ease: [0.2, 0.7, 0.2, 1] }}
            className="lp-card lp-corners p-6 sm:p-8"
          >
            <StageDetail i={active} />
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );

  // ── Mobile/tablet: vertical interactive timeline ──
  const timeline = (
    <ol className="relative mt-10 border-l border-line-strong pl-6">
      {STAGES.map((st, i) => {
        const on = i === active;
        const Icon = st.icon;
        return (
          <li key={st.key} className="relative pb-4 last:pb-0">
            <span
              className={`absolute -left-[37px] top-1.5 flex h-[26px] w-[26px] items-center justify-center rounded-full border bg-bg ${on ? "lp-glow border-accent text-accent" : "border-line-strong text-muted"}`}
              aria-hidden="true"
            >
              <Icon size={13} />
            </span>
            <button
              type="button"
              aria-expanded={on}
              aria-controls={`stage-m-${st.key}`}
              onClick={() => setActive(i)}
              className="flex w-full items-center justify-between gap-3 rounded-[3px] py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              <span>
                <span className={`mono mr-2 text-[11px] ${on ? "text-accent" : "text-faint"}`}>{String(i + 1).padStart(2, "0")}</span>
                <span className={`mono text-[13px] font-medium uppercase tracking-[0.14em] ${on ? "text-text" : "text-muted"}`}>{st.title}</span>
                <span className="mono ml-2 text-[10px] uppercase tracking-[0.1em] text-faint">{st.caption}</span>
              </span>
              <ChevronDown size={15} className={`shrink-0 text-muted transition-transform ${on ? "rotate-180" : ""}`} aria-hidden="true" />
            </button>
            <AnimatePresence initial={false}>
              {on && (
                <motion.div
                  id={`stage-m-${st.key}`}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: reduced ? 0 : 0.28 }}
                  className="overflow-hidden"
                >
                  <div className="lp-card mt-2 p-5">
                    <StageDetail i={i} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </li>
        );
      })}
    </ol>
  );

  return (
    <Section id="how-it-works" seed={2}>
      <div ref={trackRef} className={pinned ? "h-[300vh]" : ""}>
        <div className={pinned ? "sticky top-16 flex h-[calc(100vh-4rem)] flex-col justify-center" : ""}>
          {heading}
          {desktop ? desktopNodes : timeline}
          {pinned && (
            <div className="mt-6 flex items-center gap-3" aria-hidden="true">
              <span className="mono text-[10.5px] uppercase tracking-[0.14em] text-faint">Scroll to advance</span>
              <span className="h-px flex-1 bg-line">
                <motion.span className="block h-px origin-left bg-accent" style={{ scaleX: scrollYProgress }} />
              </span>
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}
