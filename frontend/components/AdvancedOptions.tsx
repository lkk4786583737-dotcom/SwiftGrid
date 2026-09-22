"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, Code2, Download } from "lucide-react";
import { downloadBlob } from "@/lib/api";
import { cn } from "@/lib/utils";

export function AdvancedOptions({ code }: { code: string }) {
  const [open, setOpen] = React.useState(false);

  function downloadCode() {
    downloadBlob(
      new Blob([code], { type: "text/x-python" }),
      "swiftgrid_logic.py",
    );
  }

  return (
    <div className="mt-4 rounded-xl border border-slate-200">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-xs font-semibold text-slate-600 smooth hover:text-brand"
      >
        <Code2 className="h-4 w-4" /> 给技术同事 · 查看/下载处理脚本（可选）
        <ChevronDown
          className={cn("ml-auto h-4 w-4 smooth", open && "rotate-180")}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="border-t border-slate-100 p-3">
              <div className="mb-2 flex justify-end">
                <button
                  onClick={downloadCode}
                  className="flex items-center gap-1.5 rounded-lg border border-brand/40 px-3 py-1.5 text-xs font-medium text-brand smooth hover:bg-brand hover:text-white"
                >
                  <Download className="h-3.5 w-3.5" /> 下载代码
                </button>
              </div>
              <pre className="max-h-72 overflow-auto rounded-lg bg-brand-dark p-4 text-[11px] leading-relaxed text-slate-100">
                <code>{code}</code>
              </pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
