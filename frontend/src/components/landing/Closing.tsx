import Link from "next/link";
import { ArrowRight, Github, ShieldAlert, UserCheck, Eye, FlaskConical } from "lucide-react";
import { Logo } from "./Logo";
import { Section, SectionHeading } from "./Section";
import { MotionCard, SectionTransition } from "./motion";

/** Set NEXT_PUBLIC_AEGIS_REPO_URL to override; the GitHub link is hidden if empty. */
const REPO_URL = process.env.NEXT_PUBLIC_AEGIS_REPO_URL ?? "https://github.com/princerm06/aegis";

const PRINCIPLES = [
  { icon: FlaskConical, title: "Simulation only", body: "Every scenario is simulated. No real patient data and no real dispatch." },
  { icon: UserCheck, title: "Humans set priority", body: "Casualty priority is entered by a person. Computer vision never assigns it." },
  { icon: ShieldAlert, title: "No automatic dispatch", body: "Approval binds to one exact revision; dispatch is a separate explicit action." },
  { icon: Eye, title: "Evidence, not diagnosis", body: "Camera observations are shown literally, with confidence and age — never as medical findings." },
];

export function About() {
  return (
    <Section id="about" seed={5}>
      <div className="grid gap-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
        <SectionHeading
          index="06"
          eyebrow="About"
          title="Built at HackPrinceton Fall 2026."
          lede="AEGIS is a hackathon prototype exploring how practical and physical AI can support — not replace — emergency incident commanders. It connects computer vision, spatial mapping, optimization, and responder hardware into one auditable loop."
        />
        <ul className="grid gap-3 sm:grid-cols-2">
          {PRINCIPLES.map((p) => (
            <MotionCard as="li" key={p.title} className="lp-card p-6">
              <p.icon size={18} className="lp-icon text-accent" aria-hidden="true" />
              <h3 className="mt-4 text-[16px] font-medium text-text">{p.title}</h3>
              <p className="mt-2 text-[14px] leading-relaxed text-muted">{p.body}</p>
            </MotionCard>
          ))}
        </ul>
      </div>
    </Section>
  );
}

export function FinalCta() {
  return (
    <section className="relative isolate overflow-hidden border-t border-line/70 py-28">
      <div className="lp-grid lp-grid-fade absolute inset-0 -z-10" aria-hidden="true" />
      <div
        className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_50%_60%_at_50%_100%,var(--accent-glow),transparent_70%)]"
        aria-hidden="true"
      />
      <SectionTransition leave={false}>
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <SectionHeading
          align="center"
          eyebrow="Command center"
          title="See the entire response loop in action."
          lede="Explore how AEGIS transforms a simulated emergency scene into coordinated, human-approved responder assignments."
        />
        <div className="mt-10 flex flex-col justify-center gap-3 sm:flex-row">
          <Link
            href="/command"
            className="inline-flex items-center justify-center gap-2 lp-press rounded-[3px] bg-accent px-6 py-3 text-[14.5px] font-medium text-on-accent hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Launch Command Center <ArrowRight size={16} aria-hidden="true" />
          </Link>
          <a
            href="#technology"
            className="inline-flex items-center justify-center gap-2 lp-press rounded-[3px] border border-line-strong px-6 py-3 text-[14.5px] text-text hover:border-accent/60 hover:bg-accent/5"
          >
            Explore the Technology
          </a>
        </div>
      </div>
      </SectionTransition>
    </section>
  );
}

export function Footer() {
  const links = [
    { href: "#platform", label: "Platform" },
    { href: "#technology", label: "Technology" },
    { href: "#about", label: "About" },
  ];
  return (
    <footer className="border-t border-line bg-panel/40">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4">
            <Logo />
            <span className="mono text-[12px] text-faint">| HackPrinceton Fall 2026</span>
          </div>
          <ul className="flex flex-wrap items-center gap-6">
            {links.map((l) => (
              <li key={l.href}>
                <a href={l.href} className="text-[13.5px] text-muted hover:text-text">
                  {l.label}
                </a>
              </li>
            ))}
            {REPO_URL && (
              <li>
                <a href={REPO_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-[13.5px] text-muted hover:text-text">
                  <Github size={14} aria-hidden="true" /> GitHub
                </a>
              </li>
            )}
          </ul>
        </div>
        <p className="mt-8 max-w-3xl border-t border-line pt-6 text-[12.5px] leading-relaxed text-faint">
          AEGIS is a simulated emergency coordination prototype. It is not clinically validated medical software and is not intended for
          real-world emergency dispatch.
        </p>
      </div>
    </footer>
  );
}
