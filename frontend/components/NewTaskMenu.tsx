"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { FilePlus2, Plus, TableProperties, WandSparkles } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export function NewTaskMenu() {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);

  function go(href: string) {
    setOpen(false);
    router.push(href);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="brand" size="sm">
          <Plus className="h-4 w-4" /> 开始新任务
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>开始一个新任务</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3 pt-2">
          <button
            onClick={() => go("/")}
            className="group flex flex-col items-center gap-3 rounded-2xl border border-slate-200 p-5 text-center smooth hover:border-brand hover:bg-brand-light/5"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-light/15 text-brand smooth group-hover:scale-110">
              <WandSparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-brand-dark">逻辑建模</div>
              <div className="mt-1 text-[11px] text-slate-400">
                上传源表 + 目标样例
              </div>
            </div>
          </button>
          <button
            onClick={() => go("/run")}
            className="group flex flex-col items-center gap-3 rounded-2xl border border-slate-200 p-5 text-center smooth hover:border-brand hover:bg-brand-light/5"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-light/15 text-brand smooth group-hover:scale-110">
              <TableProperties className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-brand-dark">
                用此逻辑处理新表
              </div>
              <div className="mt-1 text-[11px] text-slate-400">
                丢新表，自动匹配
              </div>
            </div>
          </button>
          <button
            onClick={() => go("/compose")}
            className="group flex flex-col items-center gap-3 rounded-2xl border border-slate-200 p-5 text-center smooth hover:border-brand hover:bg-brand-light/5"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-light/15 text-brand smooth group-hover:scale-110">
              <FilePlus2 className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-brand-dark">智能制表</div>
              <div className="mt-1 text-[11px] text-slate-400">
                只有源表也能制作目标表
              </div>
            </div>
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
