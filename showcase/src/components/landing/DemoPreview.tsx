import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Section, SectionHeading } from "./Section";
import { SandboxPreview } from "@/components/sandbox/SandboxPreview";

const steps = [
  "Assess a simulated emergency.",
  "Review casualty observations.",
  "Set human-controlled priorities.",
  "Generate responder recommendations.",
  "Approve and dispatch virtual responders.",
  "React to changing conditions.",
];

export function DemoPreview() {
  return (
    <Section id="interactive-demo" seed={6}>
      <div className="grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
        <div>
          <SectionHeading eyebrow="Your decisions. A changing scene." title="You're in Command. What Happens Next?" lede="Not a recording. A hands-on, private-to-your-browser command experience. No accounts, setup, or connected hardware." />
          <ol className="mt-7 grid gap-3 text-[14px] text-muted sm:grid-cols-2">
            {steps.map((step, index) => (
              <li key={step} className="flex gap-3"><span className="mono text-accent">0{index + 1}</span>{step}</li>
            ))}
          </ol>
          <Link href="/demo" className="lp-press mt-8 inline-flex items-center gap-3 rounded-[3px] bg-accent px-5 py-3 text-sm font-medium text-on-accent hover:bg-accent-hover">
            Try the Simulation <ArrowRight size={16} aria-hidden="true" />
          </Link>
          <p className="mono mt-4 text-[10px] uppercase tracking-widest text-faint">Interactive Simulation — No Real Emergency Dispatch</p>
        </div>
        <SandboxPreview />
      </div>
    </Section>
  );
}
