/**
 * Display-only layout for map markers that share one position.
 *
 * A zone-mapped camera reports everyone it sees at the centre of its room, so
 * several detections and casualties land on the exact same point and hide each
 * other. This fans them out on a small ring around the true point. It never
 * changes a position that is sent anywhere: routes, planning and the backend
 * keep using the real coordinates, and the map draws a leader line back to them.
 */

export interface SpreadItem {
  id: string;
  /** Position in map drawing units (metres). */
  x: number;
  y: number;
}

export interface SpreadOffset {
  dx: number;
  dy: number;
}

export const NO_OFFSET: SpreadOffset = { dx: 0, dy: 0 };

/**
 * @param items     markers to lay out
 * @param radius    ring radius in metres for a group of two or more
 * @param tolerance markers closer than this (per axis) count as the same point
 * @returns offset per id; markers that are alone get no entry
 */
export function spreadOffsets(items: SpreadItem[], radius = 1.5, tolerance = 0.4): Map<string, SpreadOffset> {
  const groups = new Map<string, SpreadItem[]>();
  for (const item of items) {
    if (!Number.isFinite(item.x) || !Number.isFinite(item.y)) continue;
    const key = `${Math.round(item.x / tolerance)}:${Math.round(item.y / tolerance)}`;
    const group = groups.get(key);
    if (group) group.push(item);
    else groups.set(key, [item]);
  }
  const out = new Map<string, SpreadOffset>();
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    // Sort by id so a marker keeps its slot while the group is unchanged.
    const sorted = [...group].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    const n = sorted.length;
    // Larger groups get a slightly larger ring so neighbours do not touch.
    const r = radius * (n > 6 ? 1 + (n - 6) * 0.12 : 1);
    // Two markers sit left and right; three or more start at the top.
    const start = n === 2 ? Math.PI : -Math.PI / 2;
    sorted.forEach((item, i) => {
      const angle = start + (i * 2 * Math.PI) / n;
      out.set(item.id, { dx: round2(r * Math.cos(angle)), dy: round2(r * Math.sin(angle)) });
    });
  }
  return out;
}

function round2(v: number): number {
  const r = Math.round(v * 100) / 100;
  return r === 0 ? 0 : r; // avoid -0
}
