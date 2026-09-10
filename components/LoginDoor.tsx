// Reads `?next=` in the browser, because the packaged build ships plain files and there is no server
// render to read it in. The value stays attacker-controlled either way, so it goes through the same
// safeNext funnel it always did — the containment is in that function, never in where it runs.
"use client";

import { useSearchParams } from "next/navigation";

import LoginGate from "@/components/LoginGate";
import { safeNext } from "@/lib/safe-next";

export default function LoginDoor() {
  return <LoginGate next={safeNext(useSearchParams().get("next"))} />;
}
