/**
 * Build + routing config. One knob decides the whole shape: NEXT_PUBLIC_API_URL.
 *
 * Unset (the local default) is SAME-ORIGIN PROXY MODE: /api/* is rewritten server-side, so no CORS
 * and no cross-site cookie. Set, the browser calls the backend itself. It is exported as "" and not
 * undefined so a client-side `?? fallback` cannot resurrect an absolute URL behind our backs.
 *
 * WebSockets do not survive a rewrite, so in proxy mode the voice socket dials the backend directly.
 * And a rewrite is frozen at BUILD time: changing the backend URL on a running server does nothing.
 */

import type { NextConfig } from "next";

const DIRECT_API_URL = process.env.NEXT_PUBLIC_API_URL || "";
const BACKEND_URL = process.env.KOTOBA_BACKEND_URL || "http://127.0.0.1:8000";

// The build the Python wheel carries: plain files, served by the backend, so `pip install` needs no
// Node at all. The EXPORT lands in its own distDir; the intermediates still go to `.next/`, and a
// static build alongside a running `next dev` was measured leaving it serving.
const STATIC = process.env.KOTOBA_STATIC_EXPORT === "1";

const config: NextConfig = {
  ...(STATIC ? { output: "export" as const, distDir: ".next-export" } : {}),
  // `*.node.ts` is a route that needs a Node server, so it is a route ONLY in dev. The static build
  // drops the suffix from this list and the same files stop being routes — the backend answers those
  // two paths instead, from one implementation of the signing that both sides are pinned against.
  pageExtensions: STATIC ? ["tsx", "ts", "jsx", "js"] : ["node.ts", "tsx", "ts", "jsx", "js"],
  // `next dev` serves its own resources to localhost only, so loopback BY IP — what a first-time
  // cloner types — rendered a blank page with every /_next/* refused and nothing logged.
  allowedDevOrigins: ["127.0.0.1", "::1", "[::1]"], // dev-only; `next build`/start ignore it
  // Next's gzip buffers a proxied SSE stream until it closes, so events arrived only once the stream
  // had died. Loopback needs no compression and a CDN does its own.
  compress: false,
  // pixi-live2d-display ships untranspiled ESM/old syntax — Next must transpile it.
  transpilePackages: ["pixi-live2d-display"],
  // Standalone bundle ONLY for the Docker image (Dockerfile.web sets DOCKER_BUILD=1); on Vercel we leave
  // it unset so Vercel's builder handles output + static assets.
  ...(process.env.DOCKER_BUILD ? { output: "standalone" as const } : {}),
  experimental: {
    // Proxy mode truncates a rewritten body at 10 MB by default, and a Live2D model is tens of MB: the
    // "use my own .zip" door failed with a socket hang up for every real model. The size that decides
    // is the installer's own cap; this only has to be wide enough not to be the one that refuses.
    proxyClientMaxBodySize: "200mb",
  },
  env: {
    // The packaged build answers EMPTY to every one of these, and it does so HERE rather than by
    // asking the builder to clean their shell: a value in the environment or in a `.env.local` is
    // inlined into the chunks, and one machine's address or agent id would then ship to everyone who
    // installs the wheel. Forcing them in the config beats both sources. All the readers tolerate ""
    // — the model, the entry, the scale and the anchor are runtime answers now.
    ...(STATIC ? {
      NEXT_PUBLIC_ELEVENLABS_AGENT_ID: "",
      NEXT_PUBLIC_KOTOBA_VOICE_MODE: "",
      NEXT_PUBLIC_LIVE2D_MODEL: "",
      NEXT_PUBLIC_LIVE2D_ENTRY: "",
      NEXT_PUBLIC_LIVE2D_SCALE: "",
      NEXT_PUBLIC_LIVE2D_ANCHOR_Y: "",
    } : {}),
    NEXT_PUBLIC_API_URL: STATIC ? "" : DIRECT_API_URL,
    // Empty in the packaged build so the socket dials the page's own origin: an absolute URL here is
    // frozen at build time, and a user who serves on any other port would lose voice with it.
    NEXT_PUBLIC_VOICE_WS_URL: STATIC ? "" :
      process.env.NEXT_PUBLIC_VOICE_WS_URL || (DIRECT_API_URL ? "" : BACKEND_URL.replace(/^http/, "ws")),
  },
  async headers() {
    // Framing above all: /app draws the approval card, so a page that can frame it can try to steer a
    // click into approving host execution. Deliberately not a full policy — Live2D, WebGL and the
    // inline styles need their own pass, and a half-written one that blanks the canvas is worse.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
  async rewrites() {
    if (DIRECT_API_URL) return [];
    // Plain array = afterFiles: real routes (/gate, /login, assets) always win over the proxy.
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default config;
