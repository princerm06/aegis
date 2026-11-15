"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { FEED_STATUS_LABEL, type FeedStatus } from "@/lib/feeds";
import {
  LOCAL_CAMERA_SCOPE_NOTE,
  assessMediaSupport,
  friendlyMediaError,
  mediaBlockerCopy,
  readMediaEnvironment,
  type MediaBlockerKind,
} from "@/lib/mediaCapabilities";
import { MjpegParser } from "@/lib/mjpeg";

/**
 * A live feed from a camera attached to this computer (Logitech webcam, or an
 * iPhone via macOS Continuity Camera as a stand-in bodycam) or a stream URL
 * (MJPEG renders as <img>, MP4/WebM as <video>). Optional audio input lets
 * command hear the responder. Nothing is recorded or uploaded.
 */
type Source =
  | { kind: "off" }
  | { kind: "device"; deviceId: string; audioId?: string | null }
  | { kind: "url"; url: string; as: "img" | "video" };

interface Props {
  feedId: string;
  title: string;
  overlay?: ReactNode;
  className?: string;
  compact?: boolean;
  /** Offer an audio input so command can hear the responder. */
  audio?: boolean;
  /** Shown in the header when not compact (primary viewport). */
  cameraId?: string;
  /** Reports the feed's real connection state (frames received = connected). */
  onStatus?: (feedId: string, status: FeedStatus) => void;
  /** Stream the camera registered with the backend; shown unless a source was chosen here by hand. */
  autoUrl?: string | null;
}

const RETRY_MS = 3000;
const STALL_MS = 5000;

/**
 * The registered camera's stream, read frame by frame. A stopped or stalled
 * camera shows as waiting (never a frozen "live" frame) and is reconnected.
 */
function LiveStream({ url, alt, onLive }: { url: string; alt: string; onLive: (live: boolean) => void }) {
  const [frame, setFrame] = useState<string | null>(null);
  const onLiveRef = useRef(onLive);
  onLiveRef.current = onLive;

  useEffect(() => {
    let stopped = false;
    let controller: AbortController | null = null;
    let current: string | null = null;
    const show = (bytes: Uint8Array<ArrayBuffer> | null) => {
      const next = bytes ? URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })) : null;
      setFrame(next);
      if (current) URL.revokeObjectURL(current);
      current = next;
    };
    const run = async () => {
      while (!stopped) {
        const ctl = (controller = new AbortController());
        let last = Date.now();
        const watchdog = setInterval(() => Date.now() - last > STALL_MS && ctl.abort(), 1000);
        try {
          const res = await fetch(url, { signal: ctl.signal, cache: "no-store" });
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
          const reader = res.body.getReader();
          const parser = new MjpegParser();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            for (const f of parser.push(value)) {
              last = Date.now();
              show(f);
              onLiveRef.current(true);
            }
          }
        } catch {
          /* camera stopped, unreachable, or stalled */
        } finally {
          clearInterval(watchdog);
        }
        if (stopped) break;
        show(null);
        onLiveRef.current(false);
        await new Promise((r) => setTimeout(r, RETRY_MS));
      }
    };
    void run();
    return () => {
      stopped = true;
      controller?.abort();
      if (current) URL.revokeObjectURL(current);
    };
  }, [url]);

  // eslint-disable-next-line @next/next/no-img-element
  return frame ? <img src={frame} alt={alt} className="h-full w-full object-cover" /> : null;
}

/** The source chosen by hand on this browser, or null to follow the camera's registered stream. */
function load(feedId: string): Source | null {
  try {
    const raw = localStorage.getItem(`aegis.feed.${feedId}`);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return null;
}

export function VideoFeed({ feedId, title, overlay, className = "", compact = false, audio = false, cameraId, onStatus, autoUrl = null }: Props) {
  const [saved, setSaved] = useState<Source | null>(null);
  const source = useMemo<Source>(
    () => saved ?? (autoUrl ? { kind: "url", url: autoUrl, as: "img" } : { kind: "off" }),
    [saved, autoUrl],
  );
  const auto = saved === null && !!autoUrl;
  const [attempt, setAttempt] = useState(0);
  const [videoIn, setVideoIn] = useState<MediaDeviceInfo[]>([]);
  const [audioIn, setAudioIn] = useState<MediaDeviceInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [blocker, setBlocker] = useState<MediaBlockerKind>("none");
  const [setup, setSetup] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const [muted, setMuted] = useState(true);
  const [time, setTime] = useState("");
  const [streaming, setStreaming] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;

  useEffect(() => setSaved(load(feedId)), [feedId]);
  useEffect(() => {
    const t = setInterval(() => setTime(new Date().toLocaleTimeString([], { hour12: false })), 1000);
    return () => clearInterval(t);
  }, []);

  // A new stream address (e.g. the camera restarted elsewhere) starts a fresh connection.
  const streamUrl = source.kind === "url" ? source.url : null;
  useEffect(() => {
    setStreaming(false);
    setError(null);
    setAttempt(0);
  }, [streamUrl]);

  // An unreachable stream (camera not started yet, or restarting) is retried, not given up on.
  useEffect(() => {
    if (!error || auto || source.kind !== "url" || source.as !== "img") return;
    const t = setTimeout(() => {
      setError(null);
      setAttempt((n) => n + 1);
    }, RETRY_MS);
    return () => clearTimeout(t);
  }, [error, auto, source]);

  const onLive = useCallback((on: boolean) => {
    setStreaming(on);
    setError(on ? null : "Waiting for the camera…");
  }, []);

  const choose = (s: Source | null) => {
    setSaved(s);
    setStreaming(false);
    setError(null);
    setBlocker("none");
    try {
      if (s) localStorage.setItem(`aegis.feed.${feedId}`, JSON.stringify(s));
      else localStorage.removeItem(`aegis.feed.${feedId}`);
    } catch {
      /* ignore */
    }
  };

  const refreshDevices = useCallback(async () => {
    const found = assessMediaSupport(readMediaEnvironment());
    if (found.kind !== "none") {
      setBlocker(found.kind);
      setVideoIn([]);
      setAudioIn([]);
      setError(mediaBlockerCopy(found.kind).short);
      return;
    }
    setBlocker("none");
    try {
      const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: audio });
      probe.getTracks().forEach((t) => t.stop());
      const all = await navigator.mediaDevices.enumerateDevices();
      setVideoIn(all.filter((d) => d.kind === "videoinput"));
      setAudioIn(all.filter((d) => d.kind === "audioinput"));
      setError(null);
    } catch (e) {
      setError(friendlyMediaError(e));
    }
  }, [audio]);

  useEffect(() => {
    if (source.kind !== "device") return;
    const found = assessMediaSupport(readMediaEnvironment());
    if (found.kind !== "none") {
      setBlocker(found.kind);
      setError(mediaBlockerCopy(found.kind).short);
      return;
    }
    setBlocker("none");
    let stream: MediaStream | null = null;
    let cancelled = false;
    navigator.mediaDevices
      .getUserMedia({
        video: { deviceId: { exact: source.deviceId }, width: { ideal: 1280 } },
        audio: source.audioId ? { deviceId: { exact: source.audioId }, echoCancellation: true } : false,
      })
      .then((s) => {
        if (cancelled) return s.getTracks().forEach((t) => t.stop());
        stream = s;
        if (videoRef.current) videoRef.current.srcObject = s;
      })
      .catch((e) => setError(friendlyMediaError(e)));
    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [source]);

  // "connected" only once frames actually arrive; never assumed.
  const waiting = auto && !!error; // registered camera not reachable yet: keep retrying quietly
  const status: FeedStatus =
    source.kind === "off" ? "disconnected" : waiting ? "connecting" : error ? "error" : streaming ? "connected" : "connecting";
  useEffect(() => onStatusRef.current?.(feedId, status), [feedId, status]);
  const live = source.kind !== "off" && (!error || waiting);
  const showIdle = (status === "disconnected" || status === "error") && !setup;
  const st = FEED_STATUS_LABEL[status];
  const hasAudio = (source.kind === "device" && !!source.audioId) || (source.kind === "url" && source.as === "video");
  const openSetup = () => {
    setSetup(true);
    void refreshDevices();
  };

  return (
    <div data-theme="dark" className={`group relative overflow-hidden rounded border border-line bg-black text-text ${className}`}>
      {source.kind === "device" && <video ref={videoRef} autoPlay playsInline muted={muted} onLoadedData={() => setStreaming(true)} className="h-full w-full object-cover" />}
      {source.kind === "url" && auto && <LiveStream url={source.url} alt={title} onLive={onLive} />}
      {source.kind === "url" &&
        !auto &&
        (source.as === "img" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={`${source.url}#${attempt}`}
            src={attempt ? `${source.url}${source.url.includes("?") ? "&" : "?"}retry=${attempt}` : source.url}
            alt={title}
            className="h-full w-full object-cover"
            onLoad={() => setStreaming(true)}
            onError={() => {
              setStreaming(false);
              setError("Stream not reachable");
            }}
          />
        ) : (
          <video src={source.url} autoPlay playsInline muted={muted} loop className="h-full w-full object-cover" onLoadedData={() => setStreaming(true)} onError={() => setError("Stream not playable")} />
        ))}

      {status === "connecting" && !setup && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="mono blink text-[10px] tracking-[0.2em] text-warn">{waiting ? "WAITING FOR CAMERA…" : "CONNECTING…"}</span>
        </div>
      )}

      {showIdle && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-[repeating-linear-gradient(0deg,#0a0f14_0_2px,#070b0f_2px_4px)]">
          <span className="mono text-[10px] tracking-[0.2em] text-faint">{error ? "SIGNAL ERROR" : "NO SIGNAL"}</span>
          {error && !compact && <span className="max-w-[85%] text-center text-[10.5px] text-danger">{error}</span>}
          <button
            onClick={(e) => {
              e.stopPropagation();
              openSetup();
            }}
            className="rounded border border-line-strong px-2 py-0.5 text-[10.5px] text-text hover:border-accent"
          >
            Connect
          </button>
        </div>
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-1.5 bg-gradient-to-b from-black/75 to-transparent px-1.5 py-1">
        {status === "connected" && <span className="blink h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />}
        <span className={`mono truncate font-semibold tracking-wider text-white/90 ${compact ? "text-[9.5px]" : "text-[11.5px]"}`}>{title}</span>
        {!compact && cameraId && <span className="mono shrink-0 text-[10px] text-white/45">{cameraId}</span>}
        {(!compact || status === "connected") && (
          <span className="mono shrink-0 text-[9.5px] font-semibold tracking-[0.12em]" style={{ color: st.color }} data-feed-status={status}>
            {compact ? st.short : st.full}
          </span>
        )}
        {!compact && <span className="mono ml-auto shrink-0 text-[10px] text-white/60">{time}</span>}
      </div>
      {status === "connected" && overlay && <div className="pointer-events-none absolute inset-x-0 bottom-0">{overlay}</div>}

      {live && (
        <div className="absolute right-1 top-5 flex gap-1 opacity-70 transition group-hover:opacity-100">
          {hasAudio && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setMuted((m) => !m);
              }}
              className={`mono rounded px-1 py-px text-[9.5px] ${muted ? "bg-black/60 text-white/70" : "bg-accent text-on-accent"}`}
              title={muted ? "Listen to this feed" : "Mute this feed"}
            >
              {muted ? "🔇 listen" : "🔊 on"}
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              openSetup();
            }}
            className="mono rounded bg-black/60 px-1 py-px text-[9.5px] text-white/70 hover:text-white"
          >
            src
          </button>
        </div>
      )}

      {setup && (
        <div className="absolute inset-0 z-10 flex flex-col gap-1.5 overflow-y-auto bg-[#05080bf5] p-2 text-[10.5px]" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between">
            <span className="label !text-[9.5px] !text-text">{title}</span>
            <button onClick={() => setSetup(false)} className="text-muted hover:text-text" aria-label="Close">
              ✕
            </button>
          </div>
          {autoUrl && (
            <button
              onClick={() => {
                choose(null);
                setSetup(false);
              }}
              className={`truncate rounded border px-1.5 py-0.5 text-left hover:border-accent ${auto ? "border-accent text-accent" : "border-line-strong"}`}
              title={autoUrl}
            >
              📡 Camera&apos;s own stream (automatic)
            </button>
          )}
          {blocker !== "none" ? (
            <div className="rounded border border-warn/50 bg-warn/10 p-1.5">
              <p className="font-semibold text-warn">{mediaBlockerCopy(blocker).title}</p>
              <p className="mt-0.5 leading-snug text-muted">{mediaBlockerCopy(blocker).body}</p>
            </div>
          ) : (
            <>
              {videoIn.length === 0 && <p className="text-muted">{error ?? "Requesting devices…"}</p>}
              {videoIn.map((d) => (
                <button
                  key={d.deviceId}
                  onClick={() => {
                    choose({ kind: "device", deviceId: d.deviceId, audioId: source.kind === "device" ? source.audioId ?? null : null });
                    setSetup(false);
                  }}
                  className={`truncate rounded border px-1.5 py-0.5 text-left hover:border-accent ${
                    source.kind === "device" && source.deviceId === d.deviceId ? "border-accent text-accent" : "border-line-strong"
                  }`}
                >
                  📷 {d.label || "Camera"}
                </button>
              ))}
              {audio && source.kind === "device" && audioIn.length > 0 && (
                <label className="flex items-center gap-1">
                  <span className="text-muted">hear via</span>
                  <select
                    value={source.audioId ?? ""}
                    onChange={(e) => choose({ ...source, audioId: e.target.value || null })}
                    className="mono min-w-0 flex-1 rounded border border-line-strong bg-bg px-1 py-0.5"
                  >
                    <option value="">no audio</option>
                    {audioIn.map((d) => (
                      <option key={d.deviceId} value={d.deviceId}>
                        {d.label || "microphone"}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
          <p className="text-[10px] leading-snug text-faint">{LOCAL_CAMERA_SCOPE_NOTE}</p>
          <form
            className="flex gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const u = urlDraft.trim();
              if (!u) return;
              choose({ kind: "url", url: u, as: /\.(mp4|webm|mov)(\?|$)/i.test(u) ? "video" : "img" });
              setSetup(false);
            }}
          >
            <input
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              placeholder="stream URL (.mjpg / .mp4)"
              className="mono min-w-0 flex-1 rounded border border-line-strong bg-bg px-1.5 py-0.5 outline-none focus:border-accent"
            />
            <button className="rounded border border-line-strong px-1.5 hover:border-accent">Use</button>
          </form>
          <button
            onClick={() => {
              choose({ kind: "off" });
              setSetup(false);
            }}
            className="mt-auto self-start text-muted underline hover:text-text"
          >
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}
