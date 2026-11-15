import test from "node:test";
import assert from "node:assert/strict";
import { MjpegParser } from "../src/lib/mjpeg.ts";

const enc = new TextEncoder();
const part = (bytes) =>
  new Uint8Array([
    ...enc.encode(`--aegisframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${bytes.length}\r\n\r\n`),
    ...bytes,
    ...enc.encode("\r\n"),
  ]);

test("frames come out whole however the bytes are chunked", () => {
  const a = new Uint8Array([0xff, 0xd8, 1, 2, 13, 10, 13, 10, 0xff, 0xd9]); // a CRLFCRLF inside the JPEG
  const b = new Uint8Array([0xff, 0xd8, 7, 0xff, 0xd9]);
  const stream = new Uint8Array([...part(a), ...part(b)]);
  for (const size of [1, 3, 17, stream.length]) {
    const parser = new MjpegParser();
    const frames = [];
    for (let i = 0; i < stream.length; i += size) frames.push(...parser.push(stream.subarray(i, i + size)));
    assert.deepEqual(frames, [a, b], `chunk size ${size}`);
  }
});

test("a stream without part lengths is refused instead of buffered forever", () => {
  const parser = new MjpegParser();
  const blob = new Uint8Array(5 * 1024 * 1024);
  assert.throws(() => parser.push(blob));
});
