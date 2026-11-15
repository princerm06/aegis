export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2.5 20 5.6v6.1c0 4.8-3.3 8.4-8 9.8-4.7-1.4-8-5-8-9.8V5.6L12 2.5Z" stroke="var(--accent)" strokeWidth="1.4" />
      <path d="M12 7.2v9.6M7.6 12h8.8" stroke="var(--accent)" strokeWidth="1.4" strokeLinecap="square" opacity="0.55" />
      <circle cx="12" cy="12" r="1.9" fill="var(--accent)" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="mono text-[15px] font-semibold tracking-[0.28em] text-text">AEGIS</span>
    </span>
  );
}
