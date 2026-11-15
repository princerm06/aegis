import { readFile, readdir } from "node:fs/promises";
import { resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const output = resolve(root, "out");
async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  return (await Promise.all(entries.map((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Unexpected symlink: ${path}`);
    return entry.isDirectory() ? files(path) : path;
  }))).flat();
}

const manifest = JSON.parse(await readFile(resolve(root, ".next/server/app-paths-manifest.json"), "utf8"));
if (Object.keys(manifest).sort().join(",") !== "/_not-found/page,/demo/page,/page") {
  throw new Error(`Unexpected application routes: ${Object.keys(manifest)}`);
}
const forbidden = /\/aegis-api|\/aegis-voice|["'`]\/command(?:[/?"'`]|$)|(?:127\.0\.0\.1|localhost):(?:8000|8001|8765)|AEGIS_(?:BACKEND|VOICE|ADVISORY)_(?:URL|TOKEN)|NEXT_PUBLIC_(?:AEGIS_)?(?:API|WS|BACKEND|VOICE)_URL|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|(?:sk-proj-|ghp_)[A-Za-z0-9_-]{20,}|AEGIS_PRIVATE_BUILD_SENTINEL/;
let count = 0;
for (const path of await files(output)) {
  const name = relative(output, path).replaceAll("\\", "/");
  const allowed = ["index.html", "index.txt", "demo.html", "demo.txt", "404.html", "404/index.html"].includes(name)
    || /^_next\/static\/.+\.(?:js|css|woff2?|ttf)$/.test(name);
  if (!allowed) throw new Error(`Non-allowlisted public artifact: ${name}`);
  if (/\.(?:html|txt|js|css)$/.test(name) && forbidden.test(await readFile(path, "utf8"))) {
    throw new Error(`Operational configuration or credential marker in ${name}`);
  }
  count++;
}
const networking = /\bfetch\s*\(|\b(?:WebSocket|EventSource|XMLHttpRequest)\s*\(|getUserMedia\s*\(|navigator\.(?:serial|usb|bluetooth)|process\.env|@\/(?:lib\/api|components\/CommandCenter)/;
for (const path of await files(resolve(root, "src"))) {
  const source = await readFile(path, "utf8");
  if (networking.test(source) || forbidden.test(source)) throw new Error(`Operational dependency in public source: ${relative(root, path)}`);
  for (const match of source.matchAll(/(?:from\s+|import\s*)["']([^"']+)["']/g)) {
    const specifier = match[1];
    if (specifier.startsWith("../") && !resolve(path, "..", specifier).startsWith(resolve(root, "src") + "/")) {
      throw new Error(`Import escapes showcase source: ${specifier}`);
    }
    if (!specifier.startsWith(".") && !specifier.startsWith("@/") && !/^(react|react-dom|next|framer-motion|lucide-react|tailwindcss)(?:\/|$)/.test(specifier)) {
      throw new Error(`Non-allowlisted application dependency: ${specifier}`);
    }
  }
}
console.log(`PASS: ${count} public static artifacts; only / and /demo plus 404; no operational dependencies/configuration markers.`);
