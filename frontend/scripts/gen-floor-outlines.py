"""Regenerate src/lib/floorOutlines.ts from the floor-plan drawings in scripts/floorplans/.

For each floor drawing:
  1. Footprint: ink minus thin strokes (text) -> close -> fill holes -> drop small blobs (labels).
  2. Rooms: dark wall pixels, door gaps sealed by a closing, enclosed free space labelled as rooms;
     wall pixels are then given to the nearest room so rooms tile the footprint.
  3. Registration: every drawing is mapped into one shared building frame (in ground-floor pixel
     units) using the line between the two buildings, the top of the Bendheim wing, and the scale
     of the drawing, so floors line up with each other.
  4. The frame is fitted into the 24 x 20 m scene map (letterboxed) and sampled onto a 0.25 m grid.
     Output: perimeter edges, room edges and a footprint fill, as SVG paths on grid lines.

Illustrative only: the drawings are not surveyed to the demo grid. Run from frontend/:
  uv run --no-project --with pillow --with numpy --with scipy python scripts/gen-floor-outlines.py
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as nd

MAP_W, MAP_H, CELL = 24.0, 20.0, 0.25
SRC = Path(__file__).parent / "floorplans"
OUT = Path(__file__).parent.parent / "src/lib/floorOutlines.ts"

# file, building-bounds clip (x0, y0, x1, y1 px), anchor (divider x, Bendheim top y), scale vs ground
# floor, wall darkness threshold (max RGB channel), door-sealing closing size (px)
FLOORS = {
    "A": ("level-A.png", (238, 118, 1172, 875), (803, 127), 1.38, 115, 12),
    "L1": ("level-1.png", (262, 125, 924, 700), (656, 138), 1.0, 145, 7),
    "L2": ("level-2.png", (200, 105, 1200, 890), (795, 113), 1.48, 115, 13),
    "L3": ("level-3.png", (355, 135, 1645, 1180), (1138, 145), 1.96, 115, 18),
}
# Shared frame in ground-floor pixels relative to the anchor.
FX0, FX1, FY0, FY1 = -430.0, 300.0, -40.0, 590.0


def footprint(gray: np.ndarray) -> np.ndarray:
    ink = nd.binary_opening(gray < 235, structure=np.ones((4, 4)))
    m = nd.binary_closing(ink, structure=np.ones((25, 25)))
    m = nd.binary_fill_holes(m)
    m = nd.binary_opening(m, structure=np.ones((21, 21)))
    lab, n = nd.label(m)
    sizes = nd.sum(m, lab, range(1, n + 1))
    return np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s > 0.02 * m.size])


def rooms(rgb: np.ndarray, foot: np.ndarray, scale: float, dark: int, k: int) -> np.ndarray:
    wall = (rgb.max(2) < dark) & foot
    # k seals door openings
    wall = nd.binary_closing(wall, structure=np.ones((k, k))) | ~nd.binary_erosion(foot, iterations=2)
    free = foot & ~wall
    lab, n = nd.label(free)
    sizes = nd.sum(free, lab, range(1, n + 1))
    small = np.array([0] + [s < 60 * scale * scale for s in sizes])
    lab[small[lab]] = 0
    # Give walls and slivers to the nearest room.
    _, (iy, ix) = nd.distance_transform_edt(lab == 0, return_indices=True)
    lab = lab[iy, ix]
    lab[~foot] = 0
    return lab


def sample(lab: np.ndarray, anchor, scale: float) -> np.ndarray:
    ax, ay = anchor
    mpu = min(MAP_W / (FX1 - FX0), MAP_H / (FY1 - FY0))  # metres per frame unit
    ox = (MAP_W - (FX1 - FX0) * mpu) / 2
    oy = (MAP_H - (FY1 - FY0) * mpu) / 2
    cols, rows = int(MAP_W / CELL), int(MAP_H / CELL)
    h, w = lab.shape
    out = np.zeros((rows, cols), int)
    offs = (0.25, 0.5, 0.75)
    for r in range(rows):
        for c in range(cols):
            votes = []
            for fy in offs:
                for fx in offs:
                    u = ((c + fx) * CELL - ox) / mpu + FX0
                    v = ((r + fy) * CELL - oy) / mpu + FY0
                    px, py = int(ax + u * scale), int(ay + v * scale)
                    votes.append(lab[py, px] if 0 <= px < w and 0 <= py < h else 0)
            vals, counts = np.unique(votes, return_counts=True)
            out[r, c] = vals[counts.argmax()]
    return out


def clean(g: np.ndarray) -> np.ndarray:
    # Drop isolated outside/inside slivers, then merge tiny rooms into their largest neighbour.
    for _ in range(2):
        inside = g > 0
        p = np.pad(inside, 1)
        nb = p[:-2, 1:-1].astype(int) + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
        g[inside & (nb < 2)] = 0
    for _ in range(4):
        ids, counts = np.unique(g[g > 0], return_counts=True)
        tiny = ids[counts < 5]
        if not len(tiny):
            break
        for t in tiny:
            ys, xs = np.nonzero(g == t)
            neigh = []
            for y, x in zip(ys, xs):
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < g.shape[0] and 0 <= xx < g.shape[1] and g[yy, xx] not in (0, t):
                        neigh.append(g[yy, xx])
            if neigh:
                vals, cnt = np.unique(neigh, return_counts=True)
                g[g == t] = vals[cnt.argmax()]
    return g


def walls_and_doors(g: np.ndarray):
    """Walls on every room/perimeter edge, as one-cell-thick blocked cells, plus doors.

    A crossing (r, c, "h") is the step (r, c) -> (r, c + 1); (r, c, "v") is (r, c) -> (r + 1, c).
    Wall rule: a cell is blocked when its right or lower neighbour has a different label, so every
    step between two differently labelled cells touches a blocked cell (watertight for 4-neighbour
    A*). A door unblocks the cell of one crossing whose only wall reason is that crossing.
    Doors: a maximum spanning tree over shared wall length (every room reachable), extra openings
    on long shared walls, and up to three entrances from outside. A final pass breaches a wall cell
    for any free pocket still unreachable from outside.
    """
    rows, cols = g.shape
    lab = lambda r, c: g[r, c] if 0 <= r < rows and 0 <= c < cols else None
    pairs: dict[tuple[int, int], list[tuple[int, int, str]]] = {}
    for r in range(rows):
        for c in range(cols):
            for dr, dc, o in ((0, 1, "h"), (1, 0, "v")):
                b = lab(r + dr, c + dc)
                if b is not None and b != g[r, c]:
                    pairs.setdefault(tuple(sorted((int(g[r, c]), int(b)))), []).append((r, c, o))

    def reasons(r, c):
        out = []
        if lab(r, c + 1) is not None and lab(r, c + 1) != g[r, c]:
            out.append((r, c, "h"))
        if lab(r + 1, c) is not None and lab(r + 1, c) != g[r, c]:
            out.append((r, c, "v"))
        return out

    blocked = np.zeros(g.shape, bool)
    for r in range(rows):
        for c in range(cols):
            blocked[r, c] = bool(reasons(r, c))

    def door_cells(crossings):
        """Pick a clean door (1-2 crossings) near the middle of the longest straight run."""
        ok = []
        for r, c, o in crossings:
            rr, cc = (r, c + 1) if o == "h" else (r + 1, c)
            if reasons(r, c) == [(r, c, o)] and not reasons(rr, cc):
                ok.append((r, c, o))
        if not ok:
            return []
        key = lambda x: (x[2], x[1] if x[2] == "h" else x[0], x[0] if x[2] == "h" else x[1])
        ok.sort(key=key)
        runs, cur = [], [ok[0]]
        for a, b in zip(ok, ok[1:]):
            same_line = a[2] == b[2] and (a[1] == b[1] if a[2] == "h" else a[0] == b[0])
            step = (b[0] - a[0]) if a[2] == "h" else (b[1] - a[1])
            if same_line and step == 1:
                cur.append(b)
            else:
                runs.append(cur)
                cur = [b]
        runs.append(cur)
        run = max(runs, key=len)
        m = len(run) // 2
        return run[m - 1 : m + 1] if len(run) >= 4 else [run[m]]

    # Maximum spanning tree (Kruskal) over rooms + outside, weighted by shared wall length.
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    chosen = []
    for (a, b), cr in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
        if find(a) != find(b):
            parent[find(a)] = find(b)
            chosen.append((a, b))
    chosen_set = set(chosen)
    extra = [k for k, cr in pairs.items() if k not in chosen_set and len(cr) >= 12 and k[0] != 0]
    entrances = sorted((k for k in pairs if k[0] == 0 and k not in chosen_set), key=lambda k: -len(pairs[k]))
    n_ent = sum(1 for k in chosen if k[0] == 0)
    doors: set[tuple[int, int, str]] = set()
    for k in chosen + extra + entrances[: max(0, 3 - n_ent)]:
        for d in door_cells(pairs[k]):
            doors.add(d)
            blocked[d[0], d[1]] = False

    # Guarantee: every free cell is reachable from outside (grid corner is outside).
    for _ in range(200):
        free_lab, _n = nd.label(~blocked)
        reach = free_lab == free_lab[0, 0]
        pocket = (~blocked) & ~reach
        if not pocket.any():
            break
        target = free_lab[np.argwhere(pocket)[0][0], np.argwhere(pocket)[0][1]]
        tgt = free_lab == target
        near_t = nd.binary_dilation(tgt) & blocked
        near_r = nd.binary_dilation(reach) & blocked
        cand = np.argwhere(near_t & near_r)
        if not len(cand):
            # two-cell breach: blocked cell next to the pocket whose neighbour touches reach
            cand = np.argwhere(near_t & nd.binary_dilation(near_r))
        r, c = cand[len(cand) // 2]
        blocked[r, c] = False
        doors.update(reasons(r, c))
    free_lab, _n = nd.label(~blocked)
    unreachable = int(((~blocked) & (free_lab != free_lab[0, 0])).sum())
    return blocked, doors, unreachable


def paths(g: np.ndarray, doors=frozenset()):
    rows, cols = g.shape
    c = CELL
    fmt = lambda v: f"{v:g}"
    fill, per, room = [], [], []
    for r in range(rows):
        k = 0
        while k < cols:
            if g[r, k]:
                e = k
                while e + 1 < cols and g[r, e + 1]:
                    e += 1
                fill.append(f"M{fmt(k * c)},{fmt(r * c)}h{fmt((e - k + 1) * c)}v{fmt(c)}h{fmt(-(e - k + 1) * c)}z")
                k = e + 1
            else:
                k += 1
    at = lambda r, k: g[r, k] if 0 <= r < rows and 0 <= k < cols else 0

    def edge_kind(a, b):
        if a == b:
            return None
        return "per" if (a == 0 or b == 0) else "room"

    # Horizontal edges on grid line y=r (between row r-1 and r), merged into runs; doors leave gaps.
    hk = lambda r, k: None if (r - 1, k, "v") in doors else edge_kind(at(r - 1, k), at(r, k))
    vk = lambda r, k: None if (r, k - 1, "h") in doors else edge_kind(at(r, k - 1), at(r, k))
    for r in range(rows + 1):
        for kind, bucket in (("per", per), ("room", room)):
            k = 0
            while k < cols:
                if hk(r, k) == kind:
                    e = k
                    while e + 1 < cols and hk(r, e + 1) == kind:
                        e += 1
                    bucket.append(f"M{fmt(k * c)},{fmt(r * c)}h{fmt((e - k + 1) * c)}")
                    k = e + 1
                else:
                    k += 1
    # Vertical edges on grid line x=k.
    for k in range(cols + 1):
        for kind, bucket in (("per", per), ("room", room)):
            r = 0
            while r < rows:
                if vk(r, k) == kind:
                    e = r
                    while e + 1 < rows and vk(e + 1, k) == kind:
                        e += 1
                    bucket.append(f"M{fmt(k * c)},{fmt(r * c)}v{fmt((e - r + 1) * c)}")
                    r = e + 1
                else:
                    r += 1
    return "".join(fill), "".join(per), "".join(room)


lines = [
    "// GENERATED by scripts/gen-floor-outlines.py from scripts/floorplans/*.png — do not edit by hand.",
    "// Per floor: building perimeter and room outlines on a 0.25 m grid over the 24 x 20 m scene map,",
    "// as SVG paths in map units (x east from the west edge, y south from the NORTH edge).",
    "// Illustrative only: the drawings are not surveyed to the demo grid.",
    "",
    "export interface FloorOutline {",
    "  cell_m: number;",
    "  /** Number of grid cells inside the building. */",
    "  cells: number;",
    "  rooms: number;",
    "  fill: string;",
    "  perimeter: string;",
    "  roomEdges: string;",
    "  doors: number;",
    "  /** Routing walls: one string per row from the NORTH edge, '#' = wall cell (rooms joined by doors). */",
    "  walls: string[];",
    "}",
    "",
    "export const FLOOR_OUTLINES: Record<string, FloorOutline> = {",
]
for fid, (fn, (x0, y0, x1, y1), anchor, scale, dark, k) in FLOORS.items():
    rgb = np.asarray(Image.open(SRC / fn).convert("RGB")).astype(int)
    gray = rgb.mean(2)
    clip = np.zeros(gray.shape, bool)
    clip[y0:y1, x0:x1] = True
    gray[~clip] = 255
    foot = footprint(gray)
    g = clean(sample(rooms(rgb, foot, scale, dark, k), anchor, scale))
    blocked, doors, unreachable = walls_and_doors(g)
    assert unreachable == 0, f"{fid}: {unreachable} free cells unreachable"
    fill, per, room = paths(g, doors)
    n_rooms = len(np.unique(g[g > 0]))
    print(f"{fid}: {int((g > 0).sum())} cells, {n_rooms} rooms, {len(doors)} door crossings, {int(blocked.sum())} wall cells")
    lines += [
        f"  {fid}: {{",
        f"    cell_m: {CELL},",
        f"    cells: {int((g > 0).sum())},",
        f"    rooms: {n_rooms},",
        f'    fill: "{fill}",',
        f'    perimeter: "{per}",',
        f'    roomEdges: "{room}",',
        f"    doors: {len(doors)},",
        "    walls: [",
        *[f'      "{"".join("#" if v else "." for v in row)}",' for row in blocked],
        "    ],",
        "  },",
    ]
lines.append("};")
OUT.write_text("\n".join(lines) + "\n")
print(f"wrote {OUT}")
