/**
 * WHICH model to draw, asked at startup rather than inlined by `next build` — a frozen value means a
 * prebuilt image can never change model and no setup screen can choose one. `?token=` is the only
 * credential a browser can attach to the first fetch; the backend swaps it for a cookie that pixi's
 * own relative fetches then ride.
 */
"use client";

import { useEffect, useState } from "react";

import { apiFetch, ensureAuthToken, tokenUrl } from "@/lib/api";
import { pickAvatar, type AvatarAnswer, type AvatarChoice } from "@/lib/avatar-config";

export type AvatarState = {
  /** `missing` is the fresh install: say so rather than spin on a load that will never finish. */
  status: "loading" | "ready" | "missing";
  choice: AvatarChoice | null;
  modelsDir: string;
};

export function useAvatar(apiUrl: string): AvatarState {
  const [state, setState] = useState<AvatarState>({ status: "loading", choice: null, modelsDir: "" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let answer: AvatarAnswer | null = null;
      try {
        await ensureAuthToken();
        const r = await apiFetch(`${apiUrl}/api/avatar`);
        if (r.ok) answer = (await r.json()) as AvatarAnswer;
      } catch {
        // An unreachable backend says nothing about what is installed; the legacy path is still worth trying.
      }
      if (cancelled) return;
      const choice = pickAvatar(answer, apiUrl);
      setState({
        status: choice ? "ready" : "missing",
        choice: choice && choice.served === "api" ? { ...choice, url: tokenUrl(choice.url) } : choice,
        modelsDir: answer?.models_dir || "",
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [apiUrl]);

  return state;
}
