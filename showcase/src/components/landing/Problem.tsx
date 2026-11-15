import { Layers, Scale, Timer } from "lucide-react";
import { Section, SectionHeading } from "./Section";
import { MotionCard } from "./motion";

const PROBLEMS = [
  {
    n: "01",
    icon: Layers,
    title: "Fragmented situational awareness",
    body: "Responders must interpret incomplete information from cameras, field observations, and disconnected systems.",
    visual: (
      <svg viewBox="0 0 200 72" className="h-16 w-full" aria-hidden="true">
        {[16, 64, 112, 160].map((x, i) => (
          <g key={x} opacity={0.4 + i * 0.15}>
            <rect x={x} y={14 + (i % 2) * 14} width="28" height="20" fill="none" stroke="var(--line-strong)" strokeDasharray="3 2" />
            <circle cx={x + 14} cy={24 + (i % 2) * 14} r="2" fill="var(--muted)" />
          </g>
        ))}
        <path d="M44 24 L64 38 M92 38 L112 24 M140 24 L160 38" stroke="var(--danger)" strokeOpacity="0.6" strokeDasharray="1 3" />
      </svg>
    ),
  },
  {
    n: "02",
    icon: Scale,
    title: "Resource allocation under pressure",
    body: "Limited responders must be matched to casualties based on confirmed priorities, availability, capabilities, and travel constraints.",
    visual: (
      <svg viewBox="0 0 200 72" className="h-16 w-full" aria-hidden="true">
        {[18, 36, 54].map((y) => (
          <circle key={y} cx="30" cy={y} r="4" fill="none" stroke="var(--sev-unassessed)" strokeOpacity="0.7" />
        ))}
        {[27, 45].map((y) => (
          <path key={y} d={`M171 ${y - 5} l5 9 h-10 z`} fill="var(--accent)" opacity="0.8" />
        ))}
        {[18, 36, 54].flatMap((a) =>
          [27, 45].map((b) => <path key={`${a}-${b}`} d={`M36 ${a} C100 ${a}, 100 ${b}, 164 ${b}`} fill="none" stroke="var(--line-strong)" />),
        )}
        <text x="100" y="68" textAnchor="middle" className="mono" fontSize="8" fill="var(--muted)">
          3 casualties · 2 responders
        </text>
      </svg>
    ),
  },
  {
    n: "03",
    icon: Timer,
    title: "Delayed coordination",
    body: "Recommendations are only useful when they translate into clear assignments, physical task notifications, and confirmed acknowledgments.",
    visual: (
      <svg viewBox="0 0 200 72" className="h-16 w-full" aria-hidden="true">
        <path d="M12 36 H188" stroke="var(--line-strong)" />
        {[
          [24, "var(--accent)"],
          [76, "var(--warn)"],
          [128, "var(--line-strong)"],
          [180, "var(--line-strong)"],
        ].map(([x, c]) => (
          <circle key={x as number} cx={x as number} cy="36" r="5" fill="var(--bg)" stroke={c as string} strokeWidth="1.5" />
        ))}
        <text x="24" y="58" textAnchor="middle" className="mono" fontSize="7.5" fill="var(--muted)">SEEN</text>
        <text x="76" y="58" textAnchor="middle" className="mono" fontSize="7.5" fill="var(--warn)">DECIDED?</text>
        <text x="128" y="58" textAnchor="middle" className="mono" fontSize="7.5" fill="var(--faint)">SENT?</text>
        <text x="180" y="58" textAnchor="middle" className="mono" fontSize="7.5" fill="var(--faint)">ACK?</text>
      </svg>
    ),
  },
];

export function Problem() {
  return (
    <Section id="platform" seed={1}>
      <div className="grid gap-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
        <SectionHeading
          index="01"
          eyebrow="The problem"
          title="In an emergency, information is not enough."
          lede="Most monitoring systems stop at detection and visualization. The hard part is turning what is seen into who goes where — and knowing the task actually reached them."
        />
        <ol className="grid gap-3">
          {PROBLEMS.map((p) => (
            <MotionCard as="li" key={p.n} className="lp-card grid gap-5 p-6 sm:grid-cols-[minmax(0,1fr)_200px] sm:items-center sm:p-7">
              <div>
                <div className="flex items-center gap-3">
                  <span className="mono text-[12px] text-danger/80">{p.n}</span>
                  <p.icon size={16} className="lp-icon text-muted" aria-hidden="true" />
                </div>
                <h3 className="mt-3 text-[19px] font-medium tracking-[-0.01em] text-text">{p.title}</h3>
                <p className="mt-2 text-[14.5px] leading-relaxed text-muted">{p.body}</p>
              </div>
              <div className="hidden rounded-[3px] border border-line bg-bg/60 px-2 py-1 sm:block">{p.visual}</div>
            </MotionCard>
          ))}
        </ol>
      </div>
    </Section>
  );
}
