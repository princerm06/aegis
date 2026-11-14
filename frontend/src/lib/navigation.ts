import type { AStarRoute, Position } from "./types";

/**
 * Turn an A* route (world-meter waypoints) into spoken-style directions the
 * command operator can relay to the responder. Directions are map-relative
 * (north = +y on the shared map) because responder heading is not tracked.
 * This is wayfinding help along the planned route, not a safety guarantee.
 */
export interface NavStep {
  id: number;
  text: string;
  distance_m: number;
  heading: string;
}

const COMPASS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"];

function compass(angleDeg: number): string {
  const i = Math.round((((angleDeg % 360) + 360) % 360) / 45) % 8;
  return COMPASS[i];
}

function turnPhrase(delta: number): string {
  const d = ((delta + 540) % 360) - 180; // -180..180, positive = counter-clockwise = left
  const a = Math.abs(d);
  if (a < 20) return "Continue";
  const side = d > 0 ? "left" : "right";
  if (a < 60) return `Bear ${side}`;
  if (a < 135) return `Turn ${side}`;
  return "Turn around";
}

const round = (m: number) => (m < 3 ? Math.max(0.5, Math.round(m * 2) / 2) : Math.round(m));

export function routeToSteps(route: AStarRoute, casualtyLabel: string): NavStep[] {
  const pts: Position[] = route.waypoints;
  if (pts.length < 2) return [];

  // Merge consecutive segments that share a heading.
  const legs: { angle: number; dist: number }[] = [];
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i].x_m - pts[i - 1].x_m;
    const dy = pts[i].y_m - pts[i - 1].y_m;
    const dist = Math.hypot(dx, dy);
    if (dist < 1e-6) continue;
    const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
    const last = legs[legs.length - 1];
    if (last && Math.abs(((angle - last.angle + 540) % 360) - 180) < 10) last.dist += dist;
    else legs.push({ angle, dist });
  }
  // Fold sub-meter jogs (cell-centre snapping) into the next leg.
  const long = legs.filter((l) => l.dist >= 0.9);
  const clean = long.length ? long : legs;

  const steps: NavStep[] = clean.map((l, i) => {
    const heading = compass(l.angle);
    const dist = round(l.dist);
    const verb = i === 0 ? "Head" : turnPhrase(l.angle - clean[i - 1].angle);
    const text = i === 0 ? `${verb} ${heading}, ${dist} meters` : `${verb}, go ${heading} ${dist} meters`;
    return { id: i, text, distance_m: l.dist, heading };
  });
  steps.push({ id: steps.length, text: `${casualtyLabel} should be right in front of you`, distance_m: 0, heading: "" });
  return steps;
}

/** Straight-line bearing/distance, used when no route exists. */
export function directHint(from: Position, to: Position, label: string): string {
  const dx = to.x_m - from.x_m;
  const dy = to.y_m - from.y_m;
  return `${label} is about ${round(Math.hypot(dx, dy))} meters ${compass((Math.atan2(dy, dx) * 180) / Math.PI)} (straight line, no planned route)`;
}
