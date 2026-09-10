// /login — the access gate screen. Public: it is where a browser with no credential gets one.
import { Suspense } from "react";

import LoginDoor from "@/components/LoginDoor";

export const metadata = {
  title: "Kotoba — private",
  robots: { index: false, follow: false },
};

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginDoor />
    </Suspense>
  );
}
