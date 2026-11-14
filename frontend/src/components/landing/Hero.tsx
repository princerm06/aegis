import Link from "next/link";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { BackendStatus } from "./BackendStatus";
import { InteractiveHeroScene } from "./InteractiveHeroScene";
import { SectionTransition } from "./motion";

export function Hero() {
  return (
    <section id="top" className="relative isolate overflow-hidden pt-16">
      <div className="lp-grid lp-grid-fade absolute inset-0 -z-10" aria-hidden="true" />
      <div
        className="absolute inset-x-0 top-0 -z-10 h-[620px] bg-[radial-gradient(ellipse_60%_50%_at_70%_20%,var(--accent-glow),transparent_70%)]"
        aria-hidden="true"
      />

      <SectionTransition enter={false}>
      <div className="mx-auto grid min-h-[calc(100svh-4rem)] max-w-7xl items-center gap-12 px-4 py-14 sm:px-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:gap-14 lg:px-8 lg:py-20">
        <div>
          <p className="lp-eyebrow">AI-powered emergency coordination</p>
          <h1 className="mt-5 text-balance text-[clamp(38px,5.6vw,66px)] font-semibold leading-[1.02] tracking-[-0.03em] text-text">
            From emergency detection to <span className="text-accent">coordinated action.</span>
          </h1>
          <p className="mt-6 max-w-xl text-pretty text-[17px] leading-relaxed text-muted sm:text-[18px]">
            AEGIS connects real-time scene perception, intelligent resource allocation, and human-supervised dispatch into one unified
            emergency response platform.
          </p>

          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Link
              href="/command"
              className="inline-flex items-center justify-center gap-2 lp-press rounded-[3px] bg-accent px-5 py-3 text-[14.5px] font-medium text-on-accent hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Explore Command Center <ArrowRight size={16} aria-hidden="true" />
            </Link>
            <a
              href="#how-it-works"
              className="inline-flex items-center justify-center gap-2 lp-press rounded-[3px] border border-line-strong px-5 py-3 text-[14.5px] text-text hover:border-accent/60 hover:bg-accent/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              See How It Works
            </a>
          </div>

          <div className="mt-10 space-y-3 border-t border-line pt-6">
            <p className="mono flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-faint">
              <ShieldCheck size={14} className="text-accent" aria-hidden="true" />
              Built for human-supervised emergency response
            </p>
            <BackendStatus />
          </div>
        </div>

        <InteractiveHeroScene />
      </div>
      </SectionTransition>
    </section>
  );
}
