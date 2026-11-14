/**
 * Feed model for the dashboard's primary viewport + secondary camera grid.
 * `order[0]` is the primary (large, left) feed; `order[1..]` are the secondary grid slots in reading order.
 */
export type FeedType = "map" | "bodycam" | "building-camera" | "camera";
/** Only what the feed itself reports; nothing is inferred or invented. */
export type FeedStatus = "connected" | "connecting" | "disconnected" | "error";

export interface Feed {
  id: string;
  name: string;
  type: FeedType;
  responderId?: string;
}

export const MAP_FEED_ID = "map";

/** True swap: the selected secondary feed takes the primary slot, the old primary takes the selected feed's slot. */
export function swapOrder(order: string[], selectedId: string): string[] {
  const i = order.indexOf(selectedId);
  if (i <= 0) return order; // already primary, or unknown
  const next = order.slice();
  [next[0], next[i]] = [next[i], next[0]];
  return next;
}

/** Keep the user's arrangement when the feed list changes (e.g. a responder is added); new feeds go to the end. */
export function reconcileOrder(prev: string[], ids: string[]): string[] {
  const kept = prev.filter((id) => ids.includes(id));
  for (const id of ids) if (!kept.includes(id)) kept.push(id);
  return kept;
}

export const FEED_STATUS_LABEL: Record<FeedStatus, { full: string; short: string; color: string }> = {
  connected: { full: "CONNECTED · LIVE", short: "LIVE", color: "var(--ok)" },
  connecting: { full: "CONNECTING…", short: "CONNECTING…", color: "var(--warn)" },
  disconnected: { full: "DISCONNECTED · NO SIGNAL", short: "NO SIGNAL", color: "var(--faint)" },
  error: { full: "SIGNAL ERROR", short: "SIGNAL ERROR", color: "var(--danger)" },
};
