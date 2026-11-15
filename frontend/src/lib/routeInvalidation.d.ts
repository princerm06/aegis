import type { AStarRoute, OccupancyGrid, SimulatedAssignment, SimulationState } from "./types";

export function gridCell(grid: OccupancyGrid, x: number, y: number): [number, number];
export function traversedCells(grid: OccupancyGrid, route: AStarRoute): [number, number][];
export function blockedCellsOnRoute(grid: OccupancyGrid, route: AStarRoute | null): [number, number][];
export function isRouteStale(route: AStarRoute | null, currentMapRevision: string | undefined): boolean;
export function routeAvoidsCells(grid: OccupancyGrid, route: AStarRoute | null): boolean;
export interface InvalidatedAssignment {
  assignment: SimulatedAssignment;
  blockedCells: [number, number][];
  alternativeRoute: AStarRoute | null;
}
export function getInvalidatedAssignments(state: SimulationState): InvalidatedAssignment[];
