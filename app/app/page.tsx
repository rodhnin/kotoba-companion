// Main companion route. The whole experience touches WebGL, window, and the ElevenLabs SDK
// (which needs a client-only ConversationProvider), so it is loaded with ssr:false — nothing here
// is prerendered on the server.
//
// Before any of that, one question: has this install ever been configured? A brand-new clone has no
// key, and every turn would 401 into a companion who cannot answer — so it goes to /setup instead.
// The check fails open (lib/first-run.ts), and it runs before the experience mounts rather than after,
// because mounting opens the SSE channel and a voice session against a backend with no brain.
"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { apiFetch, ensureAuthToken } from "@/lib/api";
import {
  SETUP_FACE_URL,
  alreadySentForAFace,
  beforeTheDeadline,
  firstRunNeeded,
  rememberSentForAFace,
} from "@/lib/first-run";

const CompanionExperience = dynamic(() => import("@/components/CompanionExperience"), {
  ssr: false,
});

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Fails OPEN, and the caller races it against the same deadline the first-run check uses: a refusal,
 *  a thrown fetch and a backend that ACCEPTS and then stalls must all read as "she has a face". A
 *  try/catch alone left a hung /api/avatar holding a blank page with no message on it, for ever. */
async function noFaceYet(): Promise<boolean> {
  try {
    const r = await apiFetch(`${API_URL}/api/avatar`);
    if (!r.ok) return false;
    const d = await r.json();
    return Array.isArray(d?.installed) && d.installed.length === 0;
  } catch {
    return false;
  }
}

export default function CompanionPage() {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let live = true;
    void (async () => {
      await ensureAuthToken();
      const needed = await firstRunNeeded((path) => apiFetch(`${API_URL}${path}`));
      if (!live) return;
      if (needed) {
        // First run walks the face step itself, so it counts as having been offered — without this,
        // the one visitor who reaches /app with no face is the one whose download or zip just failed,
        // and they are sent straight back to the two doors that refused them.
        rememberSentForAFace();
        return router.replace("/setup");
      }
      // A terminal install cannot fetch a model — `kotoba setup` has no browser to draw the licence
      // in — so arriving here with no face means the one screen that can end that was never seen.
      if (!alreadySentForAFace() && (await beforeTheDeadline(noFaceYet()))) {
        rememberSentForAFace();
        return router.replace(SETUP_FACE_URL);
      }
      setChecked(true);
    })();
    return () => {
      live = false;
    };
  }, [router]);

  return checked ? <CompanionExperience /> : null;
}
