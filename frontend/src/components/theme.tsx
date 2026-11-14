"use client";

import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { motion } from "framer-motion";
import { Moon, Sun } from "lucide-react";

export type Theme = "light" | "dark";
const KEY = "aegis.theme";

type ThemeCtx = { theme: Theme; setTheme: (t: Theme) => void };
const Ctx = createContext<ThemeCtx>({ theme: "dark", setTheme: () => {} });
export const useTheme = () => useContext(Ctx);

function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}
const osTheme = (): Theme => (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");

/**
 * App-wide theme (landing page + dashboard share one saved choice). The root layout's inline
 * script sets <html data-theme> before paint; this provider syncs React state, persists choices,
 * and follows the OS when nothing is saved.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");

  useLayoutEffect(() => {
    const root = document.documentElement;
    const initial = storedTheme() ?? osTheme();
    root.dataset.theme = initial;
    setThemeState(initial);

    const mq = matchMedia("(prefers-color-scheme: light)");
    const onOs = () => {
      if (storedTheme()) return;
      const t: Theme = mq.matches ? "light" : "dark";
      root.dataset.theme = t;
      setThemeState(t);
    };
    mq.addEventListener("change", onOs);
    return () => {
      mq.removeEventListener("change", onOs);
    };
  }, []);

  const setTheme = useCallback((t: Theme) => {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* storage unavailable — theme still applies for this visit */
    }
    const apply = () => {
      document.documentElement.dataset.theme = t;
      flushSync(() => setThemeState(t));
    };
    const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (doc.startViewTransition && !reduce) doc.startViewTransition(apply);
    else apply();
  }, []);

  return <Ctx.Provider value={{ theme, setTheme }}>{children}</Ctx.Provider>;
}

export function ThemeToggle({ className = "", compact = false }: { className?: string; compact?: boolean }) {
  const btn = compact ? "h-5 w-5" : "h-7 w-7";
  const { theme, setTheme } = useTheme();
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);

  const seg = (t: Theme, label: string, Icon: typeof Sun) => {
    const on = theme === t;
    return (
      <button
        type="button"
        aria-pressed={on}
        aria-label={label}
        title={label}
        onClick={() => setTheme(t)}
        className={`relative z-10 flex ${btn} items-center justify-center rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent ${
          on ? "text-accent" : "text-muted hover:text-text"
        }`}
      >
        <Icon size={compact ? 11 : 14} aria-hidden="true" />
      </button>
    );
  };

  return (
    <div role="group" aria-label="Color theme" className={`relative flex ${compact ? "h-6" : "h-8"} items-center rounded-full border border-line bg-panel/70 p-0.5 ${className}`}>
      <motion.span
        aria-hidden="true"
        className={`absolute left-0.5 top-0.5 ${btn} rounded-full bg-accent/15 ring-1 ring-accent/40`}
        animate={{ x: theme === "light" ? 0 : compact ? 20 : 28 }}
        transition={ready ? { type: "spring", stiffness: 420, damping: 32 } : { duration: 0 }}
      />
      {seg("light", "Switch to light mode", Sun)}
      {seg("dark", "Switch to dark mode", Moon)}
    </div>
  );
}
