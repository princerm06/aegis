import type { Metadata } from "next";
import { Nav } from "@/components/landing/Nav";
import { Hero } from "@/components/landing/Hero";
import { Problem } from "@/components/landing/Problem";
import { Workflow3D } from "@/components/landing/Workflow3D";
import { TechnologyArchitecture } from "@/components/landing/TechnologyArchitecture";
import { ScrollExperience } from "@/components/landing/ScrollExperience";
import { HumanLoop } from "@/components/landing/HumanLoop";
import { Hardware } from "@/components/landing/Hardware";
import { About, FinalCta, Footer } from "@/components/landing/Closing";

export const metadata: Metadata = {
  title: "AEGIS — Intelligence that moves response forward",
  description:
    "AI-powered, human-supervised emergency response coordination: computer vision, spatial mapping, optimization, and physical responder hardware in one loop. Simulation prototype.",
};

export default function LandingPage() {
  return (
    <ScrollExperience>
      <a
        href="#main"
        className="sr-only z-[60] rounded-[3px] bg-accent px-3 py-2 text-on-accent focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Skip to content
      </a>
      <Nav />
      <main id="main">
        <Hero />
        <Problem />
        <Workflow3D />
        <TechnologyArchitecture />
        <HumanLoop />
        <Hardware />
        <About />
        <FinalCta />
      </main>
      <Footer />
    </ScrollExperience>
  );
}
