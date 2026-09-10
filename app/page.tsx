/**
 * "/" — the front door. There is no marketing landing in this repository: opening Kotoba in a browser
 * means opening the companion, so the root sends you straight to /app. A server-side `redirect()`
 * rather than a next.config redirect or a client bounce — one 307 with no HTML, no flash and no
 * history entry, and the decision stays next to the route instead of in the build config. Temporary
 * on purpose: `permanentRedirect` would be a 308 that browsers cache indefinitely, and "/" is the
 * address a landing page would take back. The gate still runs afterwards, so an unauthenticated
 * visitor lands on /login.
 */
import { redirect } from "next/navigation";

export default function RootPage() {
  redirect("/app");
}
