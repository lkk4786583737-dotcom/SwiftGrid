"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BookMarked,
  CircleHelp,
  FilePlus2,
  Settings,
  TableProperties,
  WandSparkles,
} from "lucide-react";
import { Logo } from "./Logo";
import { useAppState } from "./providers";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "逻辑建模", icon: WandSparkles },
  { href: "/compose", label: "智能制表", icon: FilePlus2 },
  { href: "/run", label: "处理新表", icon: TableProperties },
  { href: "/logics", label: "我的逻辑库", icon: BookMarked },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const {
    configured,
    configurationChecked,
    modelLabel,
    setConfigOpen,
    busy,
    requestTour,
  } = useAppState();
  const [logicCount, setLogicCount] = React.useState<number | null>(null);

  // 改进项 16：逻辑库数量角标（进入任何页面时刷新一次）
  React.useEffect(() => {
    api
      .listLogics()
      .then((xs) => setLogicCount(xs.length))
      .catch(() => setLogicCount(null));
  }, [pathname]);

  // 改进项 18：运行/澄清中离开需确认
  function guardedNav(e: React.MouseEvent, href: string) {
    if (pathname === href) return;
    if (busy) {
      e.preventDefault();
      if (
        confirm("当前任务还在进行中，离开会丢失本次进度。确定离开吗？")
      ) {
        router.push(href);
      }
    }
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col justify-between border-r border-slate-200 bg-white shadow-[4px_0_24px_rgba(0,0,0,0.02)]">
      <div>
        <div className="flex h-20 items-center border-b border-slate-100 px-6">
          <Logo />
        </div>
        <nav className="space-y-1 p-4">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                onClick={(e) => guardedNav(e, href)}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 font-medium smooth",
                  active
                    ? "bg-brand-light/10 font-semibold text-brand"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {label}
                {href === "/logics" && logicCount !== null && logicCount > 0 && (
                  <span className="ml-auto rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
                    {logicCount}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="space-y-2 border-t border-slate-100 p-4">
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">
          {!configurationChecked ? (
            <>
              <span className="h-2 w-2 animate-pulse rounded-full bg-slate-300" />
              正在检查连接…
            </>
          ) : configured ? (
            <>
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-light opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
              </span>
              {modelLabel} 已连接
            </>
          ) : (
            <>
              <span className="h-2 w-2 animate-pulse rounded-full bg-amber-400" />
              <span>
                未连接 ·{" "}
                <button
                  onClick={() => setConfigOpen(true)}
                  className="font-semibold text-brand underline-offset-2 hover:underline"
                >
                  点此配置（1分钟）
                </button>
              </span>
            </>
          )}
        </div>
        <button
          onClick={() => setConfigOpen(true)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-400 smooth hover:text-slate-700"
        >
          <Settings className="h-3.5 w-3.5" /> 模型与密钥配置
        </button>
        <button
          onClick={() => {
            if (pathname !== "/") router.push("/");
            requestTour();
          }}
          className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-400 smooth hover:text-slate-700"
        >
          <CircleHelp className="h-3.5 w-3.5" /> 重看体验向导
        </button>
      </div>
    </aside>
  );
}
