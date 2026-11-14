import type { NextConfig } from "next";

// Same-origin proxy to the FastAPI backend (it intentionally has no CORS)
// and the read-only AEGIS Voice service on port 8010.
// The WebSocket connects directly to the backend; see src/lib/api.ts.
const backend = process.env.AEGIS_BACKEND_URL ?? "http://127.0.0.1:8000";
const voiceService = process.env.AEGIS_VOICE_URL ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/aegis-api/:path*", destination: `${backend}/:path*` },
      { source: "/aegis-voice/:path*", destination: `${voiceService}/:path*` },
    ];
  },
};

export default nextConfig;
