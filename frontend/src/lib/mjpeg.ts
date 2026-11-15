/**
 * Splits a multipart/x-mixed-replace MJPEG byte stream into JPEG frames. Each
 * part must carry a Content-Length header (the AEGIS CV stream does), so frame
 * arrival is known exactly; an <img> of the same stream keeps showing its last
 * frame after the camera stops and never reports it.
 */
const HEADER_END = [13, 10, 13, 10];
const MAX_BUFFER = 4 * 1024 * 1024;

function indexOf(buf: Uint8Array, pattern: number[], from = 0): number {
  outer: for (let i = from; i <= buf.length - pattern.length; i++) {
    for (let k = 0; k < pattern.length; k++) if (buf[i + k] !== pattern[k]) continue outer;
    return i;
  }
  return -1;
}

export class MjpegParser {
  #buf = new Uint8Array(0);
  #decoder = new TextDecoder();

  /** Feed received bytes; returns the frames they completed. Throws on a stream without part lengths. */
  push(chunk: Uint8Array): Uint8Array<ArrayBuffer>[] {
    const joined = new Uint8Array(this.#buf.length + chunk.length);
    joined.set(this.#buf);
    joined.set(chunk, this.#buf.length);
    let buf = joined;
    const frames: Uint8Array<ArrayBuffer>[] = [];
    for (;;) {
      const end = indexOf(buf, HEADER_END);
      if (end < 0) break;
      const length = /content-length:\s*(\d+)/i.exec(this.#decoder.decode(buf.subarray(0, end)));
      const start = end + HEADER_END.length;
      if (!length) {
        buf = buf.subarray(start);
        continue;
      }
      const size = Number(length[1]);
      if (buf.length < start + size) break;
      frames.push(buf.slice(start, start + size));
      buf = buf.subarray(start + size);
    }
    if (buf.length > MAX_BUFFER) throw new Error("MJPEG stream without part lengths");
    this.#buf = buf.slice();
    return frames;
  }
}
