import type { Metadata } from "next";
import Script from "next/script";

import "./globals.css";

export const metadata: Metadata = {
  title: "Kotoba — She talks. She listens. She does.",
  description:
    "An open-source AI companion with a Live2D face, real-time voice, and real tools. She talks back, listens, and gets things done.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Live2D Cubism 4 Core — required by pixi-live2d-display BEFORE the canvas mounts.
            Served locally (the official CDN is unreliable for production). */}
        <Script src="/live2dcubismcore.min.js" strategy="beforeInteractive" />
        {children}
      </body>
    </html>
  );
}
