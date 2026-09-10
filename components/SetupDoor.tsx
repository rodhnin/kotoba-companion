/**
 * Who is allowed through /setup. It writes her name, her language and two keys, so on a machine that
 * already works it is a screen you can only lose by — which is why it asks the same question /app
 * asks, `GET /api/setup/status`, and sends a machine that does NOT need first run to /app instead,
 * with `replace` so Back does not bounce off it. The one way in on a configured machine is the button
 * in Settings → Brain, carrying `?reconfigure=1`. A virgin install needs no invitation: `needed: true`
 * opens the screen whatever the URL says, because that is how a stranger gets in. The check fails
 * OPEN — a refusal, a thrown fetch and a backend that never answers all read as "already configured",
 * so an unreachable backend sends the visitor to /app, which reports its own trouble.
 */
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import Onboarding from "@/components/Onboarding";
import { apiFetch, ensureAuthToken } from "@/lib/api";
import { firstRunNeeded, invited, invitedStep } from "@/lib/first-run";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function SetupDoor() {
  const router = useRouter();
  const [open, setOpen] = useState<null | "first-run" | "again">(null);
  const [startAt, setStartAt] = useState("");

  useEffect(() => {
    let live = true;
    void (async () => {
      const deliberate = invited(window.location.search);
      setStartAt(invitedStep(window.location.search));
      await ensureAuthToken();
      const needed = await firstRunNeeded((path) => apiFetch(`${API_URL}${path}`));
      if (!live) return;
      if (needed) setOpen("first-run");
      else if (deliberate) setOpen("again");
      else router.replace("/app");
    })();
    return () => {
      live = false;
    };
  }, [router]);

  return open ? <Onboarding reconfigure={open === "again"} startAt={startAt} /> : null;
}
