"use client";

// 改进项 11：真·体验向导 — 导演模式 + 聚光灯 + 混合推进 + 可选第二幕（处理新表）
import * as React from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, X } from "lucide-react";
import { useAppState } from "./providers";
import { Button } from "./ui/button";
import { DEMO_STORY } from "@/lib/demoData";

type StepAction =
  | "next"
  | "fill-source"
  | "fill-target"
  | "wait-click"
  | "offer-act2"
  | "finish";

type Step = {
  id: string;
  title: string;
  body: string;
  target?: string;
  action?: StepAction;
};

const ACT1: Step[] = [
  {
    id: "welcome",
    title: "欢迎来到表速通",
    body: `${DEMO_STORY} 我们用这个真实案例，60 秒走一遍「教一次，反复用」。`,
    action: "next",
  },
  {
    id: "source",
    title: "第一步：丢进源数据表",
    body: "你平时只需要拖文件进来，或直接 Ctrl+V 粘贴表格。我先帮你填好演示数据。",
    target: "[data-tour='source-input']",
    action: "fill-source",
  },
  {
    id: "target",
    title: "第二步：给它看你的目标样例",
    body: "把你「做好的结果表」给它看，这就是在「教」它你要什么格式。",
    target: "[data-tour='target-input']",
    action: "fill-target",
  },
  {
    id: "start",
    title: "点这里，开始逻辑建模",
    body: "这一步请你亲手点一下「开始提取转换逻辑」——就像你真正使用时一样。",
    target: "[data-tour='action-button']",
    action: "wait-click",
  },
  {
    id: "timeline",
    title: "AI 正在构建你的处理逻辑",
    body: "关键节点会实时反馈。如果 AI 没把握，它会用大白话问你一句——照着答就行，不用怕。",
    target: "[data-tour='timeline']",
    action: "next",
  },
  {
    id: "result",
    title: "建模完成！这就是你的结果",
    body: "可以预览数据、看处理报告，还能一键出图表。想继续体验「处理新表」第二幕吗？",
    target: "[data-tour='result-tabs']",
    action: "offer-act2",
  },
];

const ACT2: Step[] = [
  {
    id: "run-welcome",
    title: "第二幕：处理新表",
    body: "上个月教过的逻辑已经存好了。这次只需上传新表，SwiftGrid 自动匹配并出结果。",
    action: "next",
  },
  {
    id: "run-upload",
    title: "丢一张新表进来",
    body: "和教逻辑时一样，拖入 Excel 或 Ctrl+V 粘贴。表格在本机解析，不上传云端。",
    target: "[data-tour='run-input']",
    action: "next",
  },
  {
    id: "run-match",
    title: "自动匹配已学逻辑",
    body: "如果只有一个高度吻合的逻辑，会倒计时 3 秒后自动处理——你也可以随时取消或换选。",
    target: "[data-tour='run-input']",
    action: "next",
  },
  {
    id: "run-done",
    title: "出结果，下载或继续",
    body: "处理完成后可预览、下载 Excel、出图表。下一张表点「处理下一张表」即可。",
    target: "[data-tour='run-input']",
    action: "finish",
  },
];

export function GuideTour() {
  const router = useRouter();
  const { tourRequested, setTourActive } = useAppState();
  const [open, setOpen] = React.useState(false);
  const [act, setAct] = React.useState<1 | 2>(1);
  const [idx, setIdx] = React.useState(0);
  const [rect, setRect] = React.useState<DOMRect | null>(null);

  const steps = act === 1 ? ACT1 : ACT2;
  const step = steps[idx];

  React.useEffect(() => {
    if (tourRequested > 0) {
      setAct(1);
      setIdx(0);
      setOpen(true);
      setTourActive(true);
      window.dispatchEvent(new CustomEvent("sg-tour:reset"));
    }
  }, [tourRequested, setTourActive]);

  React.useEffect(() => {
    if (!open || !step?.target) {
      setRect(null);
      return;
    }
    const el = document.querySelector(step.target);
    if (!el) {
      setRect(null);
      return;
    }
    const update = () => setRect(el.getBoundingClientRect());
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, step, idx, act]);

  React.useEffect(() => {
    if (!open) return;
    const onLearnDone = () => {
      if (step?.action === "wait-click") setIdx((i) => i + 1);
    };
    window.addEventListener("sg-tour:learn-done", onLearnDone);
    return () => window.removeEventListener("sg-tour:learn-done", onLearnDone);
  }, [open, step]);

  function close() {
    setOpen(false);
    setTourActive(false);
    setAct(1);
    setIdx(0);
    window.dispatchEvent(new CustomEvent("sg-tour:reset"));
  }

  function startAct2() {
    setAct(2);
    setIdx(0);
    router.push("/run");
  }

  function advance() {
    const action = step.action;
    if (action === "fill-source") {
      window.dispatchEvent(new CustomEvent("sg-tour:fill-source"));
    } else if (action === "fill-target") {
      window.dispatchEvent(new CustomEvent("sg-tour:fill-target"));
    } else if (action === "offer-act2" || action === "finish") {
      return;
    }
    if (idx < steps.length - 1) setIdx((i) => i + 1);
    else close();
  }

  if (!open) return null;

  const cardStyle: React.CSSProperties = rect
    ? {
        position: "fixed",
        top: Math.min(rect.bottom + 12, window.innerHeight - 200),
        left: Math.min(Math.max(rect.left, 16), window.innerWidth - 340),
        width: 320,
        zIndex: 72,
      }
    : {
        position: "fixed",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        width: 360,
        zIndex: 72,
      };

  return (
    <>
      <div
        className="fixed inset-0 z-[70] bg-brand-dark/50 backdrop-blur-[2px]"
        onClick={close}
      />
      {rect && (
        <div
          className="pointer-events-none fixed z-[71] rounded-xl ring-4 ring-brand-light/80 ring-offset-2 ring-offset-transparent"
          style={{
            top: rect.top - 6,
            left: rect.left - 6,
            width: rect.width + 12,
            height: rect.height + 12,
            boxShadow: "0 0 0 9999px rgba(11,15,20,0.55)",
          }}
        />
      )}
      <AnimatePresence mode="wait">
        <motion.div
          key={`${act}-${step.id}`}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          style={cardStyle}
          className="rounded-2xl border border-slate-100 bg-white p-5 shadow-apple"
        >
          <div className="mb-1 flex items-start justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-brand">
              {act === 1 ? "体验向导" : "第二幕"} {idx + 1}/{steps.length}
            </span>
            <button onClick={close} className="text-slate-300 hover:text-slate-500">
              <X className="h-4 w-4" />
            </button>
          </div>
          <h3 className="mb-1.5 text-sm font-bold text-brand-dark">{step.title}</h3>
          <p className="mb-4 text-xs leading-relaxed text-slate-500">{step.body}</p>

          {step.action === "wait-click" ? (
            <p className="text-center text-[11px] font-medium text-brand">
              ↑ 请点击高亮的「开始提取转换逻辑」按钮
            </p>
          ) : step.action === "offer-act2" ? (
            <div className="flex flex-col gap-2">
              <Button size="sm" className="w-full" onClick={startAct2}>
                继续体验：处理新表 <ArrowRight className="h-3.5 w-3.5" />
              </Button>
              <Button size="sm" variant="soft" className="w-full" onClick={close}>
                完成向导
              </Button>
            </div>
          ) : step.action === "finish" ? (
            <Button size="sm" className="w-full" onClick={close}>
              完成向导
            </Button>
          ) : (
            <div className="flex gap-2">
              <Button size="sm" className="flex-1" onClick={advance}>
                下一步 <ArrowRight className="h-3.5 w-3.5" />
              </Button>
              <button
                onClick={close}
                className="px-2 text-xs text-slate-400 hover:text-slate-600"
              >
                退出
              </button>
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </>
  );
}
