import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../out/", import.meta.url));
const config = JSON.parse(await readFile(new URL("../vercel.json", import.meta.url), "utf8"));
const headers = Object.fromEntries(config.headers[0].headers.map(({ key, value }) => [key, value]));
const port = Number(process.env.PORT ?? 4173);
const types = { ".html": "text/html", ".txt": "text/plain", ".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2" };
const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const route = pathname === "/" ? "index.html" : pathname === "/demo" ? "demo.html" : `.${pathname}`;
    const path = resolve(root, route);
    if (!path.startsWith(root.endsWith(sep) ? root : root + sep)) throw new Error("Outside output");
    if (!["GET", "HEAD"].includes(request.method)) {
      response.writeHead(405, headers).end();
      return;
    }
    const body = await readFile(path);
    response.writeHead(200, { ...headers, "Content-Type": types[extname(path)] ?? "application/octet-stream" });
    response.end(request.method === "HEAD" ? undefined : body);
  } catch {
    response.writeHead(404, { ...headers, "Content-Type": "text/html" });
    response.end(await readFile(resolve(root, "404.html")));
  }
});
server.on("upgrade", (_request, socket) => socket.end("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n"));
server.listen(port, "127.0.0.1", () => console.log(`Static showcase: http://127.0.0.1:${server.address().port} (no SPA fallback)`));
