"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, CircleCheck, FileSpreadsheet, RefreshCw, Repeat2 } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { SloganHeader } from "@/components/SloganHeader";
import { UniversalInput } from "@/components/UniversalInput";
import {
  ParsedTablePreview,
  PREVIEW_HEIGHT_SOURCE,
  PREVIEW_HEIGHT_TARGET,
} from "@/components/ParsedTablePreview";
import { ActionButton, type ActionState } from "@/components/ActionButton";
import { ProgressTimeline, type Step } from "@/components/ProgressTimeline";
import { HitlCard } from "@/components/HitlCard";
import { ResultTabs } from "@/components/ResultTabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAppState } from "@/components/providers";
import { useToast } from "@/components/Toast";
import { humanizeError } from "@/lib/errors";
import { scrollToElement } from "@/lib/scroll";
import { DEMO_SOURCE_PARSE, DEMO_TARGET_PARSE, DEMO_LEARN_SUCCESS } from "@/lib/demoData";
import * as api from "@/lib/api";
import type {
  ClarifyEvent,
  LearnSuccessEvent,
  ParseResp,
  ProgressEvent,
  SSEType,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type Phase = "idle" | "running" | "clarify" | "done";

function LearnFlow() {
  const router = useRouter();
  const { configured, requestConfig, setBusy, tourActive } = useAppState();
  const toast = useToast();

  const [source, setSource] = React.useState<ParseResp | null>(null);
  const [target, setTarget] = React.useState<ParseResp | null>(null);
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [progress, setProgress] = React.useState<ProgressEvent[]>([]);
  const [clarify, setClarify] = React.useState<ClarifyEvent | null>(null);
  const [result, setResult] = React.useState<LearnSuccessEvent | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [saveOpen, setSaveOpen] = React.useState(false);
  const [inputEpoch, setInputEpoch] = React.useState(0); // 重挂载上传框用

  const sidRef = React.useRef<string | null>(null);
  const streamRef = React.useRef<{ close: () => void } | null>(null);
  const tourTimers = React.useRef<ReturnType<typeof setTimeout>[]>([]);
  const actionRef = React.useRef<HTMLDivElement>(null);
  const shouldRevealActionRef = React.useRef(false);

  const ready = !!source && !!target;

  React.useEffect(() => {
    if (!shouldRevealActionRef.current || !ready || phase !== "idle") return;
    scrollToElement(actionRef.current, "end");
    shouldRevealActionRef.current = false;
  }, [ready, phase]);

  // 改进项 18：运行/澄清中标记 busy，供导航守卫
  React.useEffect(() => {
    setBusy(phase === "running" || phase === "clarify");
    return () => setBusy(false);
  }, [phase, setBusy]);

  // ── 向导（改进项 11）：监听向导事件，注入演示数据/脚本化进度 ────────────────
  React.useEffect(() => {
    function onFillSource() {
      setSource(DEMO_SOURCE_PARSE);
    }
    function onFillTarget() {
      setTarget(DEMO_TARGET_PARSE);
    }
    function onReset() {
      resetAll(false);
    }
    window.addEventListener("sg-tour:fill-source", onFillSource);
    window.addEventListener("sg-tour:fill-target", onFillTarget);
    window.addEventListener("sg-tour:reset", onReset);
    return () => {
      window.removeEventListener("sg-tour:fill-source", onFillSource);
      window.removeEventListener("sg-tour:fill-target", onFillTarget);
      window.removeEventListener("sg-tour:reset", onReset);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function playTourScript() {
    // 向导模式：不调 API，脚本化播放进度并出演示结果
    setProgress([]);
    setPhase("running");
    const fire = (ms: number, fn: () => void) =>
      tourTimers.current.push(setTimeout(fn, ms));
    fire(200, () =>
      setProgress([{ stage: "read", label: "读取源表" }]),
    );
    fire(1300, () =>
      setProgress((p) => [...p, { stage: "target", label: "理解目标样例" }]),
    );
    fire(2400, () =>
      setProgress((p) => [
        ...p,
        { stage: "infer", label: "推导处理逻辑 · 本地校验中" },
      ]),
    );
    fire(3800, () => {
      setResult(DEMO_LEARN_SUCCESS);
      setPhase("done");
      window.dispatchEvent(new CustomEvent("sg-tour:learn-done"));
    });
  }

  function openStream() {
    const sid = sidRef.current;
    if (!sid) return;
    setProgress([]);
    setSubmitting(false);
    setPhase("running");
    streamRef.current = api.streamSSE(
      `/api/learn/stream/${sid}`,
      (type: SSEType, data: unknown) => {
        if (type === "progress") {
          setProgress((p) => [...p, data as ProgressEvent]);
        } else if (type === "clarify") {
          setClarify(data as ClarifyEvent);
          setPhase("clarify");
        } else if (type === "success") {
          setResult(data as LearnSuccessEvent);
          setSaved(false);
          setPhase("done");
        } else if (type === "error") {
          setPhase("idle");
          toast.error(humanizeError((data as { message: string }).message), () =>
            start(),
          );
        } else if (type === "cancelled") {
          setPhase("idle");
          toast.info("已中断", "输入都还在，可以直接再次开始。");
        }
      },
      "learn",
    );
  }

  async function start() {
    if (tourActive) {
      playTourScript();
      return;
    }
    // 改进项 12：未配置 → 弹配置，配置完自动继续
    if (!configured) {
      requestConfig(() => start());
      return;
    }
    if (!ready) return;
    try {
      const { session_id } = await api.learnStart({
        source_handle: source!.handle,
        target_handle: target!.handle,
      });
      sidRef.current = session_id;
      setClarify(null);
      setResult(null);
      setSaved(false);
      openStream();
    } catch (e) {
      if (api.isConfigRequired(e)) {
        requestConfig(() => start());
        return;
      }
      toast.error(humanizeError(e), () => start());
    }
  }

  async function cancel() {
    streamRef.current?.close();
    tourTimers.current.forEach(clearTimeout);
    if (sidRef.current) await api.learnCancel(sidRef.current).catch(() => {});
    setPhase("idle");
  }

  // 改进项 1：澄清态放弃出口
  async function abortClarify() {
    streamRef.current?.close();
    if (sidRef.current) await api.learnCancel(sidRef.current).catch(() => {});
    setClarify(null);
    setPhase("idle");
    toast.info("已放弃本次学习", "文件还在下面，检查或更换后可重新开始。");
  }

  async function retry(clarification: string) {
    if (!sidRef.current) return;
    setSubmitting(true);
    try {
      await api.learnClarify(sidRef.current, clarification);
      openStream();
    } catch (e) {
      setSubmitting(false);
      toast.error(humanizeError(e), () => retry(clarification));
    }
  }

  // 改进项 3/4：一键重新开始（清干净所有状态 + 重挂上传框）
  function resetAll(notify = true) {
    streamRef.current?.close();
    tourTimers.current.forEach(clearTimeout);
    if (sidRef.current) api.learnCancel(sidRef.current).catch(() => {});
    sidRef.current = null;
    setSource(null);
    setTarget(null);
    setClarify(null);
    setResult(null);
    setSaved(false);
    setProgress([]);
    setPhase("idle");
    setInputEpoch((n) => n + 1);
    if (notify) toast.info("已重新开始", "上传新的源表和目标样例即可。");
  }

  React.useEffect(
    () => () => {
      streamRef.current?.close();
      tourTimers.current.forEach(clearTimeout);
    },
    [],
  );

  const buttonState: ActionState =
    phase === "running"
      ? "running"
      : phase === "clarify"
        ? "waiting"
        : phase === "done"
          ? "done"
          : "idle";

  function onButton() {
    if (phase === "idle") void start();
    else if (phase === "running") void cancel();
    else if (phase === "done" && !saved) setSaveOpen(true);
  }

  function onSourceParsed(next: ParseResp | null) {
    setSource(next);
    if (next && target) shouldRevealActionRef.current = true;
  }

  function onTargetParsed(next: ParseResp | null) {
    setTarget(next);
    if (source && next) shouldRevealActionRef.current = true;
  }

  const steps = buildSteps(progress, phase);
  const showRestart = phase !== "idle" || ready;

  return (
    <>
      <div className="flex items-start justify-between">
        <SloganHeader
          title="构建可复用的数据处理逻辑"
          subtitle="丢进源表，拿走结果。"
          highlight="你的企业级数据对齐流水线。"
        />
        {/* 改进项 3：随时重新开始 */}
        {showRestart && (
          <div className="shrink-0 px-8 pt-10">
            <button
              onClick={() => {
                if (
                  phase === "idle" ||
                  confirm("重新开始会清空当前任务，确定吗？")
                )
                  resetAll();
              }}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-500 smooth hover:border-brand/40 hover:text-brand"
            >
              <RefreshCw className="h-3.5 w-3.5" /> 重新开始
            </button>
          </div>
        )}
      </div>

      <div className="flex w-full flex-1 flex-col px-8 pb-10">
        <div
          className={cn(
            "mx-auto flex w-full flex-1 flex-col smooth",
            phase === "idle" ? "max-w-2xl" : "max-w-6xl",
          )}
        >
          <div className="flex flex-1 flex-col gap-1.5 rounded-2xl bg-white p-1.5 shadow-apple">
            <div className="flex min-h-0 flex-1 flex-col gap-1.5 lg:flex-row">
              {/* 输入列 */}
              <div
                className={cn(
                  "flex w-full flex-col rounded-xl border border-slate-100 bg-slate-50/60 p-6 smooth",
                  (phase === "running" || phase === "clarify") && "lg:w-[40%] lg:min-w-[320px]",
                  phase === "done" && "lg:w-[280px] lg:shrink-0",
                )}
              >
              <div className="flex-1 space-y-5">
                {/* 改进项 4/5：已解析的槽位显示摘要卡 + 换文件；未解析显示上传框 */}
                <InputSlot
                  key={`src-${inputEpoch}`}
                  slot="source"
                  label="① 源数据表"
                  parsed={source}
                  locked={phase !== "idle"}
                  onParsed={onSourceParsed}
                  onClear={() => setSource(null)}
                  tourId="source-input"
                />
                <InputSlot
                  key={`tgt-${inputEpoch}`}
                  slot="target"
                  label="② 目标表样例"
                  badge="期望的最终格式"
                  compact
                  parsed={target}
                  locked={phase !== "idle"}
                  onParsed={onTargetParsed}
                  onClear={() => setTarget(null)}
                  tourId="target-input"
                />
              </div>

                {phase !== "done" && (
                  <div
                    ref={actionRef}
                    className={cn(
                      "mt-auto pt-4",
                      (ready || phase !== "idle") && "border-t border-slate-200/70",
                    )}
                  >
                    <div data-tour="action-button">
                      <ActionButton
                        state={buttonState}
                        onClick={onButton}
                        disabled={phase === "idle" && !ready}
                      />
                    </div>
                    {phase === "idle" && !ready && (
                      <p className="mt-2 text-center text-[11px] text-slate-400">
                        上传/粘贴上面两份表格后即可开始
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* 右侧：进度 / 澄清 / 结果 */}
              <AnimatePresence mode="wait">
              {(phase === "running" || phase === "clarify") && (
                <motion.div
                  key="run"
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0 }}
                  className="flex w-full min-w-0 flex-col rounded-xl bg-white p-6 lg:flex-1"
                  data-tour="timeline"
                >
                  <ProgressTimeline steps={steps} />
                  {phase === "clarify" && clarify && (
                    <>
                      <HitlCard
                        data={clarify}
                        submitting={submitting}
                        onRetry={retry}
                        onAbort={abortClarify}
                      />
                      <p className="mt-3 text-center text-[11px] text-slate-400">
                        确认后 AI 会重新推导并本地校验，通过才出表
                      </p>
                    </>
                  )}
                </motion.div>
              )}

              {phase === "done" && result && (
                <motion.div
                  key="done"
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex w-full min-w-0 flex-col rounded-xl bg-white p-6 lg:flex-1"
                  data-tour="result-tabs"
                >
                  <ResultTabs
                    resultPreview={result.result_preview}
                    beforePreview={source?.sheets}
                    resultHandle={result.result_handle}
                    code={result.code}
                    banner={`逻辑建模完成 · ${result.suggested_name}`}
                  />
                </motion.div>
              )}
              </AnimatePresence>
            </div>

            <AnimatePresence>
              {phase === "done" && result && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="rounded-xl border-t border-slate-100 bg-slate-50/70 p-4"
                >
                  <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-brand-dark">
                    <CircleCheck className="h-3.5 w-3.5 text-brand" />
                    {saved ? "已存入逻辑库，下个月新表来了直接用。" : "确认无误后保存为可复用逻辑。"}
                  </div>
                  <div data-tour="action-button">
                    <ActionButton
                      state="done"
                      onClick={onButton}
                      disabled={saved}
                      doneLabel={saved ? "已保存" : "保存到逻辑库"}
                    />
                  </div>
                  {saved && (
                    <motion.div
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="mt-3 grid gap-2 sm:grid-cols-2"
                    >
                      <Button
                        size="sm"
                        className="w-full"
                        onClick={() => router.push("/run")}
                      >
                        去处理新表 <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="soft"
                        className="w-full"
                        onClick={() => resetAll(false)}
                      >
                        <Repeat2 className="h-3.5 w-3.5" /> 再建一个
                      </Button>
                    </motion.div>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      <SaveDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        defaultName={result?.suggested_name ?? ""}
        onSave={async (name) => {
          if (tourActive) {
            setSaved(true);
            setSaveOpen(false);
            return;
          }
          if (!sidRef.current) return;
          try {
            await api.learnSave(sidRef.current, name);
            setSaved(true); // 改进项 19：防重复保存
            setSaveOpen(false);
            toast.success("保存成功", `「${name}」已进入你的逻辑库。`);
          } catch (e) {
            toast.error(humanizeError(e));
          }
        }}
      />
    </>
  );
}

/** 输入槽：未解析→万能上传框；已解析→摘要卡 + 换文件（改进项 4/5） */
function InputSlot({
  slot,
  label,
  badge,
  compact,
  parsed,
  locked,
  onParsed,
  onClear,
  tourId,
}: {
  slot: "source" | "target";
  label: string;
  badge?: string;
  compact?: boolean;
  parsed: ParseResp | null;
  locked: boolean;
  onParsed: (r: ParseResp | null) => void;
  onClear: () => void;
  tourId: string;
}) {
  if (parsed && !locked) {
    return (
      <div data-tour={tourId}>
        <div className="mb-2 flex items-center justify-between">
          <label className="text-sm font-semibold text-slate-800">{label}</label>
          {badge && (
            <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-400">
              {badge}
            </span>
          )}
        </div>
        <ParsedTablePreview
          resp={parsed}
          previewHeight={compact ? PREVIEW_HEIGHT_TARGET : PREVIEW_HEIGHT_SOURCE}
          onClear={onClear}
        />
      </div>
    );
  }

  if (parsed) {
    const s = parsed.sheets[0];
    return (
      <div data-tour={tourId}>
        <div className="mb-2 flex items-center justify-between">
          <label className="text-sm font-semibold text-slate-800">{label}</label>
          {!locked && (
            <button
              onClick={onClear}
              className="text-[11px] font-medium text-slate-400 smooth hover:text-brand"
            >
              换文件
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 rounded-xl border border-brand-light/30 bg-brand-light/5 px-3 py-2.5 text-xs font-medium text-brand-dark">
          <FileSpreadsheet className="h-4 w-4 shrink-0 text-brand" />
          <span className="truncate">
            {s ? `${s.name} · ${s.total_rows} 行 × ${s.columns.length} 列` : "已识别"}
          </span>
          {parsed.sheets.length > 1 && (
            <span className="ml-auto shrink-0 text-[10px] text-slate-400">
              共 {parsed.sheets.length} 表
            </span>
          )}
        </div>
      </div>
    );
  }
  return (
    <div data-tour={tourId}>
      <UniversalInput
        slot={slot}
        label={label}
        badge={badge}
        compact={compact}
        onParsed={onParsed}
      />
    </div>
  );
}

function buildSteps(progress: ProgressEvent[], phase: Phase): Step[] {
  const steps: Step[] = progress.map((p, i) => ({
    label: p.label,
    status:
      phase === "done"
        ? "done"
        : i < progress.length - 1
          ? "done"
          : phase === "clarify"
            ? "done"
            : "active",
  }));
  if (phase === "clarify") {
    steps.push({ label: "需要你的一句确认，AI 才能继续", status: "warn" });
  }
  if (steps.length === 0) {
    steps.push({ label: "正在连接…", status: "active" });
  }
  return steps;
}

function SaveDialog({
  open,
  onOpenChange,
  defaultName,
  onSave,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  defaultName: string;
  onSave: (name: string) => Promise<void>;
}) {
  const [name, setName] = React.useState(defaultName);
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => setName(defaultName), [defaultName]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>保存这条逻辑</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 pt-1">
          <label className="text-xs font-semibold text-slate-700">逻辑名称</label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
          <Button
            size="lg"
            className="w-full"
            disabled={saving || !name.trim()}
            onClick={async () => {
              setSaving(true);
              await onSave(name.trim());
              setSaving(false);
            }}
          >
            {saving ? "保存中…" : "保存到我的逻辑库"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function Page() {
  return (
    <AppShell>
      <LearnFlow />
    </AppShell>
  );
}
