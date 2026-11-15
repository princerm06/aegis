import test from "node:test";
import assert from "node:assert/strict";
import { buildingStreamUrl } from "../src/lib/feeds.ts";

test("BUILDING CAM follows the first registered non-body camera that serves a view", () => {
  const room = { camera_id: "room-cam-1", stream_url: "http://192.168.137.145:8090/stream.mjpg" };
  assert.equal(buildingStreamUrl([]), null);
  assert.equal(buildingStreamUrl([{ camera_id: "phone-1", stream_url: null }, room]), room.stream_url);
  assert.equal(buildingStreamUrl([{ camera_id: "bodycam-1", stream_url: "http://10.0.0.9:8091/stream.mjpg" }, room]), room.stream_url);
  assert.equal(buildingStreamUrl([{ camera_id: "room-cam-1" }]), null); // older backend: no field
});

test("only plain http(s) addresses are shown", () => {
  assert.equal(buildingStreamUrl([{ camera_id: "cam", stream_url: "javascript:alert(1)" }]), null);
  assert.equal(buildingStreamUrl([{ camera_id: "cam", stream_url: "https://askar-mac.ts.net/stream.mjpg" }]), "https://askar-mac.ts.net/stream.mjpg");
});
