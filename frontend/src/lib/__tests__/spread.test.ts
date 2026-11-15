import { describe, expect, it } from "vitest";
import { spreadOffsets } from "../spread";

const at = (id: string, x: number, y: number) => ({ id, x, y });
const dist = (a: { dx: number; dy: number }, b: { dx: number; dy: number }) => Math.hypot(a.dx - b.dx, a.dy - b.dy);

describe("spreadOffsets", () => {
  it("leaves markers at distinct positions alone", () => {
    const out = spreadOffsets([at("a", 4, 8), at("b", 12, 5), at("c", 18, 14)]);
    expect(out.size).toBe(0);
  });

  it("puts two markers at one point left and right of it", () => {
    const out = spreadOffsets([at("b", 14, 8), at("a", 14, 8)], 1.5);
    expect(out.get("a")).toEqual({ dx: -1.5, dy: 0 });
    expect(out.get("b")).toEqual({ dx: 1.5, dy: 0 });
  });

  it("fans a larger group out on a ring with no two markers overlapping", () => {
    const items = ["t1", "t2", "t3", "t4", "t5", "t6"].map((id) => at(id, 14, 8));
    const out = spreadOffsets(items, 1.5);
    expect(out.size).toBe(6);
    const offsets = [...out.values()];
    for (const o of offsets) expect(Math.hypot(o.dx, o.dy)).toBeCloseTo(1.5, 1);
    for (let i = 0; i < offsets.length; i++)
      for (let j = i + 1; j < offsets.length; j++) expect(dist(offsets[i], offsets[j])).toBeGreaterThan(1.2);
  });

  it("only moves the markers that share a point", () => {
    const out = spreadOffsets([at("a", 14, 8), at("b", 14.1, 8.05), at("far", 3, 2)]);
    expect(out.has("a")).toBe(true);
    expect(out.has("b")).toBe(true);
    expect(out.has("far")).toBe(false);
  });

  it("is stable regardless of input order", () => {
    const one = spreadOffsets([at("a", 1, 1), at("b", 1, 1), at("c", 1, 1)]);
    const two = spreadOffsets([at("c", 1, 1), at("a", 1, 1), at("b", 1, 1)]);
    expect([...one.entries()].sort()).toEqual([...two.entries()].sort());
  });

  it("ignores markers with invalid coordinates", () => {
    const out = spreadOffsets([at("a", Number.NaN, 1), at("b", 1, 1), at("c", 1, 1)]);
    expect(out.has("a")).toBe(false);
    expect(out.size).toBe(2);
  });
});
