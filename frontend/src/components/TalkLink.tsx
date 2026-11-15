"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { assessMediaSupport, readMediaEnvironment } from "@/lib/mediaCapabilities";
import {
  MIC,
  OPERATOR_TOKEN_KEY,
  POLL_MS,
  RING_TIMEOUT_MS,
  fetchRtcConfig,
  isTalkAuthError,
  operatorStep,
  phoneLink,
  phoneOrigin,
  readToken,
  talkApi,
  talkErrorText,
  waitForIce,
  writeToken,
  type Enrollment,
} from "@/lib/talk";

/** connecting: getting the mic + building the offer · ringing: offer sent, waiting for the phone · live: audio connected. */
type Phase = "idle" | "connecting" | "ringing" | "live";
/** Whether this browser may use the talk link at all. */
type Access = "checking" | "signin" | "unconfigured" | "ok";

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const session = () => (typeof window === "undefined" ? undefined : window.sessionStorage);

/**
 * Responder talk link — a live two-way voice call with the selected responder's phone (WebRTC).
 * Not Ask AEGIS: that assistant only answers questions; this connects the operator to a person.
 *
 * Signed in with the operator secret (typed once, kept in sessionStorage for this tab only). The
 * phone joins with a per-responder link minted here (`enroll`); the link is the phone's credential,
 * so it is hidden once the phone is connected and can be revoked.
 */
export function TalkLink({ responderId, name, onLog }: { responderId: string; name: string; onLog: (t: string, k: "voice") => void }) {
  const [access, setAccess] = useState<Access>("checking");
  const [secretInput, setSecretInput] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [peer, setPeer] = useState(name);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [notice, setNotice] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [copied, setCopied] = useState(false);
  const tokenRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const call = useRef<{ channel: string; who: string; session: string | null; answered: boolean; startedAt: number; liveAt: number } | null>(null);
  const api = useMemo(() => talkApi(undefined, () => tokenRef.current), []);

  const signOut = useCallback((why: string) => {
    tokenRef.current = null;
    writeToken(session(), OPERATOR_TOKEN_KEY, null);
    setAccess("signin");
    setOnline(null);
    setNotice({ text: why, tone: "error" });
  }, []);

  const end = useCallback(
    (why: { text: string; tone: "info" | "error" } | null, tellPhone: boolean, keepalive = false) => {
      const c = call.current;
      if (!c) return;
      call.current = null;
      if (tellPhone && c.session) void api.hangup(c.channel, c.session, "operator", keepalive).catch(() => {});
      pcRef.current?.close();
      pcRef.current = null;
      micRef.current?.getTracks().forEach((t) => t.stop());
      micRef.current = null;
      if (audioRef.current) audioRef.current.srcObject = null;
      onLog(c.liveAt ? `Talk with ${c.who} (${clock((Date.now() - c.liveAt) / 1000)})` : `Talk with ${c.who} not connected`, "voice");
      setMuted(false);
      setPhase("idle");
      setNotice(why ?? (c.liveAt ? { text: `Call with ${c.who} ended.`, tone: "info" } : null));
    },
    [api, onLog],
  );

  // Ends the call (and tells the phone) when this component goes away or the page is closed.
  useEffect(() => {
    const onHide = () => end(null, true, true);
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      end(null, true, true);
    };
  }, [end]);

  // Sign-in state for this tab. No talk request is ever sent without credentials: with no secret
  // stored, ask for it first (a host without the talk link answers 503 once signed in).
  useEffect(() => {
    tokenRef.current = readToken(session(), OPERATOR_TOKEN_KEY);
    setAccess(tokenRef.current ? "ok" : "signin");
  }, []);

  // One poll: access, the selected responder's presence, or the active call's progress.
  useEffect(() => {
    let stopped = false;
    let t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const c = call.current;
      let delay = POLL_MS;
      if (!tokenRef.current) {
        // Signed out: nothing to poll until the operator signs in (re-run when access becomes "ok").
        return;
      }
      try {
        const view = await api.status(c?.channel ?? responderId);
        if (stopped) return;
        setAccess("ok");
        if (!c) {
          setOnline(view.responder_online);
          if (view.responder_online) setEnrollment(null); // phone joined: hide the link (it is the credential)
        } else if (c.session && call.current === c) {
          const step = operatorStep(c.session, c.answered, view);
          if (step === "apply-answer" && view.session?.answer) {
            c.answered = true;
            await pcRef.current?.setRemoteDescription({ type: "answer", sdp: view.session.answer }).catch((e: unknown) => {
              if (call.current === c) end({ text: `Couldn't connect to ${c.who}'s phone: ${talkErrorText(e)}`, tone: "error" }, true);
            });
          } else if (step === "close") {
            end(
              view.session?.ended_by === "responder"
                ? { text: `${c.who} ended the call / left the talk link.`, tone: "info" }
                : { text: `Call with ${c.who} disconnected.`, tone: "info" },
              false,
            );
          } else if (!c.answered && Date.now() - c.startedAt > RING_TIMEOUT_MS) {
            end({ text: `${c.who}'s phone didn't pick up. Is the AEGIS responder page open on it?`, tone: "error" }, true);
          }
        }
      } catch (e) {
        if (stopped) return;
        if (isTalkAuthError(e)) {
          if (c) end(null, false);
          if (e.kind === "unconfigured") {
            setAccess("unconfigured");
            delay = 15_000; // check again occasionally in case the host is configured later
          } else if (e.kind === "signin" && !tokenRef.current) {
            setAccess("signin");
            delay = 15_000;
          } else {
            signOut(e.kind === "forbidden" ? "That secret isn't the operator's. Talk link sign-in required." : "Talk link sign-in required — the secret was rejected or has changed.");
            delay = 15_000;
          }
        } else if (!c) setOnline(null);
      }
      if (!stopped) t = setTimeout(tick, delay);
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(t);
    };
  }, [responderId, end, api, signOut, access === "ok"]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (phase !== "live") return;
    const t = setInterval(() => setElapsed(call.current?.liveAt ? (Date.now() - call.current.liveAt) / 1000 : 0), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const submitSecret = (e: React.FormEvent) => {
    e.preventDefault();
    const s = secretInput.trim();
    if (!s) return;
    tokenRef.current = s;
    writeToken(session(), OPERATOR_TOKEN_KEY, s);
    setSecretInput("");
    setNotice(null);
    setAccess("ok"); // the next poll verifies it (401/403 → back to sign-in)
  };

  const getPhoneLink = async () => {
    setNotice(null);
    try {
      setEnrollment(await api.enroll(responderId));
      setCopied(false);
    } catch (e) {
      if (isTalkAuthError(e) && e.kind !== "unconfigured") signOut("Talk link sign-in required.");
      else setNotice({ text: talkErrorText(e), tone: "error" });
    }
  };

  const revokePhone = async () => {
    if (!window.confirm(`Sign out ${name}'s phone? Its link stops working and any call ends.`)) return;
    try {
      await api.revoke(responderId);
      setEnrollment(null);
      setOnline(false);
      setNotice({ text: `${name}'s phone was signed out. Get a new link to reconnect it.`, tone: "info" });
    } catch (e) {
      setNotice({ text: talkErrorText(e), tone: "error" });
    }
  };

  const start = async () => {
    if (assessMediaSupport(readMediaEnvironment()).kind !== "none") {
      setNotice({ text: "Talking needs this browser's microphone: open the dashboard as http://localhost:3000 on the host, or over HTTPS.", tone: "error" });
      return;
    }
    const c = { channel: responderId, who: name, session: null as string | null, answered: false, startedAt: Date.now(), liveAt: 0 };
    call.current = c;
    setPeer(name);
    setNotice(null);
    setElapsed(0);
    setPhase("connecting");
    try {
      const [mic, config] = await Promise.all([navigator.mediaDevices.getUserMedia({ audio: MIC }), fetchRtcConfig(undefined, tokenRef.current)]);
      if (call.current !== c) {
        mic.getTracks().forEach((t) => t.stop());
        return;
      }
      micRef.current = mic;
      const pc = new RTCPeerConnection(config);
      pcRef.current = pc;
      mic.getAudioTracks().forEach((t) => pc.addTrack(t, mic));
      pc.ontrack = (e) => {
        if (!audioRef.current) return;
        audioRef.current.srcObject = e.streams[0] ?? new MediaStream([e.track]);
        void audioRef.current.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (call.current !== c) return;
        if (pc.connectionState === "connected" && !c.liveAt) {
          c.liveAt = Date.now();
          setPhase("live");
        } else if (pc.connectionState === "failed") {
          end({ text: "Audio couldn't connect. Put the phone and this computer on the same network (hotspot or Tailscale), or configure a TURN relay.", tone: "error" }, true);
        }
      };
      await pc.setLocalDescription(await pc.createOffer());
      await waitForIce(pc);
      if (call.current !== c) return;
      const view = await api.offer(c.channel, pc.localDescription?.sdp ?? "");
      c.session = view.session?.session_id ?? null;
      if (call.current === c) setPhase("ringing");
    } catch (e) {
      if (call.current !== c) return;
      if (isTalkAuthError(e) && e.kind !== "unconfigured") {
        end(null, false);
        signOut("Talk link sign-in required.");
      } else end({ text: talkErrorText(e), tone: "error" }, true);
    }
  };

  const toggleMute = () => {
    const next = !muted;
    micRef.current?.getAudioTracks().forEach((t) => (t.enabled = !next));
    setMuted(next);
  };

  const origin = typeof window === "undefined" ? null : phoneOrigin(process.env.NEXT_PUBLIC_AEGIS_PHONE_ORIGIN, window.location);
  const link = enrollment ? phoneLink(origin ?? window.location.origin, enrollment) : null;

  return (
    <div className="rounded border border-line bg-panel-2 p-2" data-testid="talk-link" data-access={access} data-phase={phase} data-online={String(online)}>
      <audio ref={audioRef} autoPlay playsInline hidden />
      <p className="label mb-1 flex items-center gap-1.5">
        <span>📞 Responder talk link</span>
        <span className="mono text-[9px] font-normal normal-case tracking-normal text-faint">two-way call · not Ask AEGIS</span>
      </p>

      {access === "unconfigured" ? (
        <p className="text-[10.5px] text-muted" data-testid="talk-unconfigured">
          Talk link is not configured on this host (the host needs AEGIS_TALK_OPERATOR_TOKEN).
        </p>
      ) : access === "signin" ? (
        <form onSubmit={submitSecret} className="flex flex-col gap-1" data-testid="talk-signin">
          <span className="text-[10.5px] text-warn">Talk link sign-in required — enter the operator secret (kept for this tab only).</span>
          <div className="flex gap-1">
            <input
              type="password"
              value={secretInput}
              onChange={(e) => setSecretInput(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-label="Talk link operator secret"
              data-testid="talk-secret"
              className="min-w-0 flex-1 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
              placeholder="Operator secret"
            />
            <button type="submit" className="rounded border border-accent/60 px-2 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10" data-testid="talk-signin-submit">
              Sign in
            </button>
          </div>
        </form>
      ) : phase === "idle" ? (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={start}
              disabled={!online || access !== "ok"}
              data-testid="talk-start"
              className="whitespace-nowrap rounded border border-accent/60 px-2.5 py-1 text-[11px] font-semibold text-accent hover:bg-accent/10 disabled:cursor-not-allowed disabled:border-line-strong disabled:text-faint"
            >
              📞 Talk to {name}
            </button>
            <span className="flex items-center gap-1 text-[10.5px] text-muted" data-testid="talk-presence">
              <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: online ? "var(--accent)" : "var(--faint)" }} />
              {access === "checking" || online === null ? "checking phone…" : online ? "phone ready" : "phone not connected"}
            </span>
            {online && (
              <button onClick={revokePhone} data-testid="talk-revoke" className="ml-auto text-[10px] text-faint underline hover:text-text">
                sign out phone
              </button>
            )}
          </div>
          {online === false && access === "ok" && !link && (
            <button onClick={getPhoneLink} data-testid="talk-enroll" className="self-start text-[10.5px] text-accent underline">
              Get {name}&apos;s phone link
            </button>
          )}
          {link && (
            <div className="rounded border border-line bg-bg p-1.5 text-[10px] leading-snug" data-testid="talk-phone-link">
              <p className="text-muted">Open on {name}&apos;s phone, then tap Join. Treat it like a password — it&apos;s hidden once the phone connects.</p>
              <p className="mono mt-0.5 break-all select-all text-text" data-testid="talk-phone-url">
                {link}
              </p>
              <div className="mt-1 flex gap-2">
                <button
                  onClick={() => void navigator.clipboard?.writeText(link).then(() => setCopied(true), () => setCopied(false))}
                  className="text-accent underline"
                >
                  {copied ? "copied" : "copy"}
                </button>
                <button onClick={() => setEnrollment(null)} className="text-faint underline">
                  hide
                </button>
              </div>
              {!origin && (
                <p className="mt-0.5 text-warn">
                  This dashboard is on plain HTTP: the phone&apos;s microphone needs the HTTPS (Tailscale) address — open the same path there.
                </p>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <span className={`mono flex-1 text-[11.5px] font-bold ${phase === "live" ? "text-accent" : "text-warn"}`} data-testid="talk-status" role="status">
            {phase === "live"
              ? `● CONNECTED · ${peer.toUpperCase()} · ${clock(elapsed)}${muted ? " · YOU'RE MUTED" : ""}`
              : phase === "ringing"
                ? `Ringing ${peer}'s phone…`
                : `Connecting to ${peer}'s phone…`}
          </span>
          {phase === "live" && (
            <button
              onClick={toggleMute}
              aria-pressed={muted}
              data-testid="talk-mute"
              className={`mono rounded border px-2 py-1 text-[10.5px] uppercase ${muted ? "border-warn text-warn" : "border-line-strong text-muted hover:text-text"}`}
            >
              {muted ? "unmute" : "mute"}
            </button>
          )}
          <button onClick={() => end(null, true)} data-testid="talk-end" className="rounded border border-danger bg-danger px-2.5 py-1 text-[11px] font-bold text-white">
            {phase === "live" ? "End" : "Cancel"}
          </button>
        </div>
      )}

      {notice && (
        <p className={`mt-1 text-[10.5px] leading-snug ${notice.tone === "error" ? "text-danger" : "text-muted"}`} data-testid="talk-notice">
          {notice.text}
        </p>
      )}
    </div>
  );
}
