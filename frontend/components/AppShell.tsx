"use client";

import * as React from "react";
import { MonitorUp } from "lucide-react";
import { AppProviders } from "./providers";
import { Sidebar } from "./Sidebar";
import { LogoMark } from "./Logo";
import { ConfigDialog } from "./ConfigDialog";
import { OnboardingModal } from "./OnboardingModal";
import { GuideTour } from "./GuideTour";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AppProviders>
      <div className="flex min-h-screen flex-col items-center justify-center px-8 text-center md:hidden">
        <LogoMark className="mb-5 h-14 w-14" />
        <MonitorUp className="mb-3 h-5 w-5 text-brand" />
        <h1 className="text-lg font-bold text-brand-dark">请在电脑上使用表速通</h1>
        <p className="mt-2 max-w-xs text-sm leading-6 text-slate-500">
          当前版本面向 Excel 桌面处理，使用电脑可以完整查看宽表、分析和图表。
        </p>
      </div>
      <div className="hidden h-screen w-full overflow-hidden text-[14px] md:flex">
        <Sidebar />
        <main className="relative flex h-full flex-1 flex-col overflow-y-auto">
          {children}
        </main>
        <ConfigDialog />
        <OnboardingModal />
        <GuideTour />
      </div>
    </AppProviders>
  );
}
