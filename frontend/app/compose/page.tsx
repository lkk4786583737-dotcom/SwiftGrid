"use client";

import { Suspense } from "react";
import { AppShell } from "@/components/AppShell";
import { AiNativeSmartTableWorkspace } from "@/components/compose/AiNativeSmartTableWorkspace";

function ComposePageInner() {
  return <AiNativeSmartTableWorkspace />;
}

export default function ComposePage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <ComposePageInner />
      </Suspense>
    </AppShell>
  );
}
