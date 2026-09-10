/**
 * /setup — first run: the onboarding screens wired to the backend. Every answer is saved the moment
 * it is given, and "Wake her up" lands in /app. NOT reachable at any time any more: a machine that
 * does not need first run is redirected to /app, and the single way back in is the Reconfigure button
 * in Settings → Brain. The password gate runs first, so this screen can never 401 its way through a
 * configured instance. A server component on purpose: the title is route metadata, because a
 * `document.title` written from an effect loses to Next's own <title>, and the tab kept the layout's
 * marketing name.
 */
import type { Metadata } from "next";

import SetupDoor from "@/components/SetupDoor";

export const metadata: Metadata = { title: "Kotoba — first meeting" };

export default function SetupPage() {
  return <SetupDoor />;
}
