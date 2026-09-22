"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import { LogoMark } from "./Logo";
import { Button } from "@/components/ui/button";
import { useAppState } from "./providers";

const SEEN_KEY = "swiftgrid_onboarded";

export function OnboardingModal() {
  const { requestTour } = useAppState();
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    try {
      if (!localStorage.getItem(SEEN_KEY)) setOpen(true);
    } catch {
      /* ignore */
    }
  }, []);

  function dismiss() {
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
    setOpen(false);
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-brand-dark/45 backdrop-blur-md"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="w-full max-w-lg overflow-hidden rounded-3xl bg-white shadow-[0_25px_50px_-12px_rgba(0,0,0,.25)]"
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 260, damping: 22 }}
          >
            {/* 上：无代码演示动画 */}
            <div className="relative flex h-48 flex-col items-center justify-center overflow-hidden border-b border-slate-100 bg-slate-50 p-6">
              <div className="absolute inset-0 bg-gradient-to-br from-brand-light/10 to-slate-50" />
              <div className="relative z-10 w-full max-w-xs space-y-3">
                {[0, 0.4, 0.8].map((delay, i) => (
                  <motion.div
                    key={i}
                    className="h-3 rounded-full bg-brand"
                    initial={{ width: "28%", backgroundColor: "#e2e8f0" }}
                    animate={{ width: "100%", backgroundColor: "#0F8A4D" }}
                    transition={{
                      duration: 1.6,
                      delay,
                      repeat: Infinity,
                      repeatType: "reverse",
                      ease: "easeInOut",
                    }}
                  />
                ))}
              </div>
              <div className="absolute bottom-4 rounded-full bg-brand-light/15 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-brand">
                100% 自动化对齐
              </div>
            </div>

            {/* 下：文案 + 操作 */}
            <div className="p-8 text-center">
              <LogoMark className="mx-auto mb-4 h-12 w-12" />
              <h2 className="mb-2 text-2xl font-bold text-brand-dark">
                欢迎来到 SwiftGrid 表速通
              </h2>
              <p className="mb-2 text-sm leading-relaxed text-slate-500">
                丢进源表，拿走结果。
                <br />
                只需上传一次样例，AI 帮你沉淀可复用的数据处理逻辑。
              </p>
              <p className="mb-7 text-[11px] tracking-wide text-slate-400">
                Drop in Data. Take out Results.
              </p>
              <div className="flex flex-col gap-3">
                <Button
                  size="lg"
                  onClick={() => {
                    dismiss();
                    requestTour();
                  }}
                  className="w-full"
                >
                  开始使用向导 <ArrowRight className="h-4 w-4" />
                </Button>
                <button
                  onClick={dismiss}
                  className="w-full py-2 text-sm font-medium text-slate-400 smooth hover:text-slate-600"
                >
                  跳过，我已经熟悉了
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
