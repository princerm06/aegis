"use client";

import type { ReactNode } from "react";
import { MotionConfig } from "framer-motion";
import { FloatingSectionNavigation } from "./FloatingSectionNavigation";

/** Client shell for the landing page: motion preferences and section navigation. */
export function ScrollExperience({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      {children}
      <FloatingSectionNavigation />
    </MotionConfig>
  );
}
