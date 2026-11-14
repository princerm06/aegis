import type { ButtonHTMLAttributes, ReactNode } from "react";

export function PanelHeader({ title, right, sub }: { title: string; right?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="label !text-text">{title}</h2>
        {sub && <span className="truncate text-[11px] text-muted">{sub}</span>}
      </div>
      {right}
    </div>
  );
}

type Tone = "neutral" | "accent" | "warn" | "ok" | "danger" | "info";

const TONE: Record<Tone, string> = {
  neutral: "border-line-strong text-muted",
  accent: "border-accent/50 text-accent",
  warn: "border-warn/50 text-warn",
  ok: "border-ok/50 text-ok",
  danger: "border-danger/50 text-danger",
  info: "border-info/50 text-info",
};

export function Badge({ tone = "neutral", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={`mono inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-[10px] uppercase tracking-wider ${TONE[tone]} ${className}`}>
      {children}
    </span>
  );
}

type Variant = "primary" | "ghost" | "danger" | "ok" | "warn";

const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-on-accent hover:brightness-110 border-accent",
  ok: "bg-ok text-on-ok hover:brightness-110 border-ok",
  warn: "bg-warn text-on-warn hover:brightness-110 border-warn",
  danger: "bg-transparent text-danger border-danger/60 hover:bg-danger/10",
  ghost: "bg-transparent text-text border-line-strong hover:border-muted hover:bg-panel-2",
};

export function Button({
  variant = "ghost",
  size = "md",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" | "lg" }) {
  const sz = size === "sm" ? "px-2 py-0.5 text-[11px]" : size === "lg" ? "px-4 py-2.5 text-sm" : "px-3 py-1.5 text-xs";
  return (
    <button
      {...props}
      className={`rounded border font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${sz} ${VARIANT[variant]} ${className}`}
    />
  );
}

export function Dot({ color, className = "" }: { color: string; className?: string }) {
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${className}`} style={{ background: color }} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-3 py-4 text-center text-xs text-muted">{children}</p>;
}
