'use client';

import { CORRIDORS, HEIGHT, WIDTH, type Action, type SandboxState } from '../../lib/sandbox/engine';

const horizontal = (value: number) => 40 + value * 64;
const vertical = (value: number) => 40 + value * 64;

export function SandboxMap({ state, onAction }: { state: SandboxState; onAction?: (action: Action) => void }) {
  const visibleTasks = state.tasks.filter(task => !['done', 'replaced', 'blocked'].includes(task.status));
  const advisoryRoutes = state.plan?.assignments ?? [];
  const routes = [
    ...visibleTasks.filter(task => !advisoryRoutes.some(assignment => assignment.responderId === task.responderId)),
    ...advisoryRoutes,
  ];
  return (
    <svg viewBox="0 0 464 336" className="block w-full" role="group" aria-label="Synthetic incident grid. Alpha and Bravo start west. Four synthetic casualties lie east and south. Crossing controls toggle blocked map edges.">
      <title>Synthetic incident grid — no real locations or devices</title>
      <defs><pattern id="sandbox-dots" width="16" height="16" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="currentColor" opacity=".12" /></pattern></defs>
      <rect width="464" height="336" rx="16" fill="url(#sandbox-dots)" />
      {Array.from({ length: HEIGHT }, (_, row) => Array.from({ length: WIDTH }, (_, column) => (
        <g key={`${row}-${column}`} stroke="currentColor" opacity=".17">
          {column < WIDTH - 1 && <line x1={horizontal(column)} y1={vertical(row)} x2={horizontal(column + 1)} y2={vertical(row)} />}
          {row < HEIGHT - 1 && <line x1={horizontal(column)} y1={vertical(row)} x2={horizontal(column)} y2={vertical(row + 1)} />}
          <circle cx={horizontal(column)} cy={vertical(row)} r="2" fill="currentColor" />
        </g>
      )))}
      {routes.map(route => (
        <polyline key={route.responderId} points={route.route.map(point => `${horizontal(point.x)},${vertical(point.y)}`).join(' ')} fill="none" stroke={route.responderId === 'alpha' ? 'var(--info)'  : 'var(--accent)' } strokeWidth="4" strokeDasharray={advisoryRoutes.includes(route) ? '6 5' : undefined} opacity=".85"><title>{route.responderId} to {route.casualtyId}: {route.route.length - 1} grid steps; {advisoryRoutes.includes(route) ? 'advisory only' : 'virtual dispatched route'}</title></polyline>
      ))}
      {CORRIDORS.map(corridor => {
        const blocked = state.blocked.includes(corridor.id);
        return <g key={corridor.id}>
          {corridor.edges.map(segment => {
            const [start, end] = segment.split('|').map(point => point.split(',').map(Number));
            const centerX = (horizontal(start[0]) + horizontal(end[0])) / 2;
            const centerY = (vertical(start[1]) + vertical(end[1])) / 2;
            return <line key={segment} x1={centerX} y1={centerY - 9} x2={centerX} y2={centerY + 9} stroke={blocked ? 'var(--danger)'  : 'var(--ok)' } strokeWidth="5" />;
          })}
          <g role={onAction ? 'button' : 'img'} tabIndex={onAction ? 0 : undefined} aria-label={`${corridor.label}: ${blocked ? 'blocked' : 'open'}${onAction ? '. Toggle crossing' : ''}`} aria-pressed={onAction ? blocked : undefined} data-testid={`map-corridor-${corridor.id}`} onClick={() => onAction?.({ type: 'corridor', id: corridor.id })} onKeyDown={event => { if (onAction && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onAction({ type: 'corridor', id: corridor.id }); } }} className={onAction ? 'cursor-pointer outline-offset-4 focus-visible:outline focus-visible:outline-2' : ''}>
            <rect x={corridor.id === 'central' ? 144 : 304} y="3" width="112" height="27" rx="7" fill="var(--panel, #17232b)" stroke={blocked ? 'var(--danger)'  : 'var(--ok)' } />
            <text x={corridor.id === 'central' ? 200 : 360} y="21" textAnchor="middle" fill={blocked ? 'var(--danger)'  : 'var(--ok)' } fontSize="10" fontFamily="monospace">{corridor.id === 'central' ? 'CENTRAL' : 'EAST'} · {blocked ? 'BLOCKED' : 'OPEN'}</text>
          </g>
        </g>;
      })}
      {state.casualties.map(casualty => <g key={casualty.id} role="img" aria-label={`${casualty.id}, ${casualty.label}, ${casualty.confirmed ? 'confirmed' : 'unconfirmed'}, priority ${casualty.priority}`}>
        <rect x={horizontal(casualty.position.x) - 14} y={vertical(casualty.position.y) - 14} width="28" height="28" rx="8" fill="var(--panel, #17232b)" stroke={casualty.confirmed ? 'var(--warn)'  : 'var(--muted)' } strokeDasharray={casualty.confirmed ? undefined : '3 3'} strokeWidth="2" />
        <text x={horizontal(casualty.position.x)} y={vertical(casualty.position.y) + 4} textAnchor="middle" fontSize="10" fill="currentColor" fontFamily="monospace">{casualty.id}</text>
      </g>)}
      {state.responders.map(responder => <g key={responder.id} role="img" aria-label={`${responder.name}, virtual ${responder.board}, ${responder.available ? 'available' : 'offline'}`}>
        <circle cx={horizontal(responder.position.x)} cy={vertical(responder.position.y)} r="17" fill="var(--panel, #17232b)" stroke={responder.id === 'alpha' ? 'var(--info)'  : 'var(--accent)' } strokeWidth="2" />
        <text x={horizontal(responder.position.x)} y={vertical(responder.position.y) + 4} textAnchor="middle" fontSize="10" fill="currentColor" fontFamily="monospace">{responder.name.slice(0, 1)}</text>
      </g>)}
      <text x="24" y="329" fill="currentColor" opacity=".65" fontFamily="monospace" fontSize="9">WEST STAGING → EAST ACCESS / SYNTHETIC GRID</text>
    </svg>
  );
}
